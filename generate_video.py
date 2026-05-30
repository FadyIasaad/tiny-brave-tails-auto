import asyncio
import json
import math
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import quote_plus

import arabic_reshaper
import edge_tts
import requests
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFilter, ImageFont
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.LANCZOS
from moviepy.editor import AudioFileClip, ImageClip, concatenate_videoclips, vfx

from tbt_common import (
    get_sheets_client, open_spreadsheet, get_worksheet, get_all_values,
    update_cell, find_column, get_cell, log, require_env
)

CONTENT_SHEET_NAME = "Content"
LOGS_SHEET_NAME = "Logs"
OUTPUT_DIR = Path("output")
FRAMES_DIR = OUTPUT_DIR / "frames"
VISUALS_DIR = OUTPUT_DIR / "visuals"
AUDIO_DIR = OUTPUT_DIR / "audio"
for folder in [OUTPUT_DIR, FRAMES_DIR, VISUALS_DIR, AUDIO_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

WIDTH, HEIGHT, FPS = 1080, 1920, 24
DEFAULT_VOICE = os.getenv("EDGE_TTS_VOICE", "en-US-JennyNeural")
BEDTIME_VOICE = os.getenv("EDGE_TTS_BEDTIME_VOICE", "en-US-AriaNeural")

EMOTION_STYLE = {
    "wonder": {"voice": BEDTIME_VOICE, "rate": "-8%", "pitch": "+0Hz", "volume": "+0%"},
    "lonely": {"voice": BEDTIME_VOICE, "rate": "-15%", "pitch": "-4Hz", "volume": "-2%"},
    "worried": {"voice": BEDTIME_VOICE, "rate": "-12%", "pitch": "-2Hz", "volume": "+0%"},
    "afraid": {"voice": BEDTIME_VOICE, "rate": "-10%", "pitch": "-5Hz", "volume": "+1%"},
    "brave": {"voice": DEFAULT_VOICE, "rate": "-6%", "pitch": "+1Hz", "volume": "+2%"},
    "relieved": {"voice": DEFAULT_VOICE, "rate": "-9%", "pitch": "+0Hz", "volume": "+0%"},
    "peaceful": {"voice": BEDTIME_VOICE, "rate": "-14%", "pitch": "-3Hz", "volume": "-1%"},
}

def load_font(size, bold=True, arabic=False):
    paths = []
    if arabic:
        paths += ["/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf", "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf"]
    paths += ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for path in paths:
        if Path(path).exists(): return ImageFont.truetype(path, size)
    return ImageFont.load_default()

def reshape_arabic(text):
    if not text: return ""
    return get_display(arabic_reshaper.reshape(text))

def wrap_ltr(draw, text, font, max_width, max_lines=3):
    words = str(text or "").split()
    lines, current = [], ""
    for word in words:
        test = (current + " " + word).strip()
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            current = test
        else:
            if current: lines.append(current)
            current = word
            if len(lines) >= max_lines: break
    if current and len(lines) < max_lines: lines.append(current)
    return lines[:max_lines]

def wrap_arabic(draw, text, font, max_width, max_lines=3):
    words = str(text or "").split()
    lines, current = [], ""
    for word in words:
        test = (current + " " + word).strip()
        if draw.textbbox((0, 0), reshape_arabic(test), font=font)[2] <= max_width:
            current = test
        else:
            if current: lines.append(current)
            current = word
            if len(lines) >= max_lines: break
    if current and len(lines) < max_lines: lines.append(current)
    return [reshape_arabic(line) for line in lines]

def draw_centered_lines(draw, lines, font, center_y, fill, spacing=9):
    total_h = sum([draw.textbbox((0, 0), l, font=font)[3]-draw.textbbox((0, 0), l, font=font)[1] for l in lines]) + spacing*(len(lines)-1)
    y = center_y - total_h // 2
    for line in lines:
        w = draw.textbbox((0, 0), line, font=font)[2]
        draw.text(((WIDTH-w)//2 + 4, y + 4), line, font=font, fill=(0,0,0,210))
        draw.text(((WIDTH-w)//2, y), line, font=font, fill=fill)
        y += (draw.textbbox((0, 0), line, font=font)[3]-draw.textbbox((0, 0), line, font=font)[1]) + spacing

def pollinations_image(prompt, output_path, seed):
    final_prompt = f"warm 2D cartoon storybook illustration, consistent cute animal, soft lighting, vertical 9:16, no text. Scene: {prompt}"
    url = f"https://image.pollinations.ai/prompt/{quote_plus(final_prompt)}?width={WIDTH}&height={HEIGHT}&seed={seed}&nologo=true&enhance=true&model=flux"
    r = requests.get(url, timeout=150)
    r.raise_for_status()
    output_path.write_bytes(r.content)

def make_frame(video_id, i, scene, title, image_path, total):
    img = Image.open(image_path).convert("RGB").resize((WIDTH, HEIGHT), Image.LANCZOS)
    draw = ImageDraw.Draw(img, "RGBA")
    draw.rectangle((0, 0, WIDTH, 230), fill=(0, 0, 0, 100))
    draw.text((55, 36), "Tiny Brave Tails", font=load_font(42), fill=(255, 238, 190))
    y = 98
    for line in wrap_ltr(draw, title, load_font(30, False), 940, 2):
        draw.text((55, y), line, font=load_font(30, False), fill=(245, 245, 245))
        y += 38

    draw.rectangle((40, HEIGHT-490, WIDTH-40, HEIGHT-75), fill=(0,0,0,150))
    en_lines = wrap_ltr(draw, scene.get("subtitle_en") or scene.get("narration_en"), load_font(48), 910, 2)
    ar_lines = wrap_arabic(draw, scene.get("subtitle_ar", ""), load_font(43, arabic=True), 910, 2)
    draw_centered_lines(draw, en_lines, load_font(48), HEIGHT-360, (255, 255, 255))
    draw_centered_lines(draw, ar_lines, load_font(43, arabic=True), HEIGHT-190, (255, 232, 170))

    frame_path = FRAMES_DIR / f"frame_{video_id}_{i:02d}.jpg"
    img.save(frame_path, quality=95)
    return frame_path

async def create_audio(text, path, emotion):
    s = EMOTION_STYLE.get(emotion, EMOTION_STYLE["peaceful"])
    c = edge_tts.Communicate(text=text, voice=s["voice"], rate=s["rate"], pitch=s["pitch"])
    await c.save(str(path))

def create_video(video_id, title, payload):
    scenes = payload["scenes"]
    char_desc = payload["character"]["description"]
    safe_id = re.sub(r"\W+", "_", video_id)
    clips = []
    for i, s in enumerate(scenes, 1):
        v_path = VISUALS_DIR / f"visual_{safe_id}_{i:02d}.jpg"
        pollinations_image(f"{char_desc}. {s['image_prompt']}", v_path, i + 100)
        a_path = AUDIO_DIR / f"audio_{safe_id}_{i:02d}.mp3"
        asyncio.run(create_audio(s["narration_en"], a_path, s["emotion"]))
        audio = AudioFileClip(str(a_path))
        f_path = make_frame(safe_id, i, s, title, v_path, len(scenes))
        clip = ImageClip(str(f_path)).set_duration(audio.duration + 0.4).set_audio(audio)
        clip = clip.resize(lambda t: 1 + 0.04 * t / clip.duration)
        clips.append(clip)

    v_path = OUTPUT_DIR / f"tiny_brave_tails_{safe_id}.mp4"
    concatenate_videoclips(clips, method="compose").write_videofile(str(v_path), fps=FPS, bitrate="8000k")
    return v_path

def main():
    require_env("GOOGLE_SHEET_ID")
    require_env("GOOGLE_SERVICE_ACCOUNT_JSON")
    client = get_sheets_client()
    spreadsheet = open_spreadsheet(client)
    sheet = get_worksheet(spreadsheet, CONTENT_SHEET_NAME)
    vals = get_all_values(sheet)
    headers = vals[0]
    status_col = find_column(headers, "status")
    for i, row in enumerate(vals[1:], 2):
        if get_cell(row, status_col).upper() == "GENERATED":
            id_val = get_cell(row, find_column(headers, "id"))
            title = get_cell(row, find_column(headers, "title"))
            payload = json.loads(get_cell(row, find_column(headers, "scene_prompts")))
            path = create_video(id_val, title, payload)
            update_cell(sheet, i, status_col, "VIDEO_CREATED")
            update_cell(sheet, i, find_column(headers, "video_file_path"), str(path))
            break

if __name__ == "__main__":
    main()
