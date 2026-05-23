import asyncio
import json
import math
import os
import random
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import edge_tts
import gspread
import requests
from gtts import gTTS
from google.oauth2.service_account import Credentials
from moviepy.editor import AudioFileClip, ImageClip, concatenate_videoclips, vfx
from PIL import Image, ImageDraw, ImageFilter, ImageFont


SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

CONTENT_SHEET_NAME = "Content"
LOGS_SHEET_NAME = "Logs"

OUTPUT_DIR = Path("output")
FRAMES_DIR = OUTPUT_DIR / "frames"
VISUALS_DIR = OUTPUT_DIR / "visuals"
AUDIO_DIR = OUTPUT_DIR / "audio"

OUTPUT_DIR.mkdir(exist_ok=True)
FRAMES_DIR.mkdir(parents=True, exist_ok=True)
VISUALS_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

WIDTH = 1080
HEIGHT = 1920
FPS = 24

VOICE_BY_EMOTION = {
    "curious": ("en-US-AriaNeural", "+4%", "+10%"),
    "sad": ("en-US-JennyNeural", "-4%", "-5%"),
    "fear": ("en-US-JennyNeural", "-2%", "+0%"),
    "brave": ("en-US-GuyNeural", "+2%", "+8%"),
    "happy": ("en-US-AriaNeural", "+5%", "+8%"),
    "emotional": ("en-US-AriaNeural", "+0%", "+4%"),
}

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_sheets_client():
    service_account_info = json.loads(SERVICE_ACCOUNT_JSON)
    credentials = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
    return gspread.authorize(credentials)


def find_column(headers, name):
    if name not in headers:
        raise ValueError(f"Missing required column: {name}")
    return headers.index(name) + 1


def find_optional_column(headers, name):
    return headers.index(name) + 1 if name in headers else None


def get_cell(row, col):
    return row[col - 1].strip() if col and len(row) >= col else ""


def update_optional(sheet, row_number, col, value):
    if col:
        sheet.update_cell(row_number, col, value)


def log(logs_sheet, video_id, action, message):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    logs_sheet.append_row([now, video_id, action, message], value_input_option="USER_ENTERED")


def safe_filename(value):
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "video")).strip("_")
    return value or "video"


def load_font(size, bold=True):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def wrap_ltr(draw, text, font, max_width, max_lines=3):
    text = re.sub(r"\s+", " ", str(text or "").strip())
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines[:max_lines]


def draw_centered_lines(draw, lines, font, center_y, fill, spacing=10, shadow=True):
    heights = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        heights.append(bbox[3] - bbox[1])
    total_h = sum(heights) + spacing * max(0, len(lines) - 1)
    y = center_y - total_h // 2
    for line, h in zip(lines, heights):
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        x = (WIDTH - w) // 2
        if shadow:
            draw.text((x + 5, y + 5), line, font=font, fill=(0, 0, 0, 235))
        draw.text((x, y), line, font=font, fill=fill)
        y += h + spacing


def draw_badge(draw, text, x, y, font, fill=(255, 238, 190, 255)):
    text = re.sub(r"\s+", " ", str(text or "").strip())[:34]
    if not text:
        return
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0] + 46
    h = bbox[3] - bbox[1] + 28
    draw.rounded_rectangle((x, y, x + w, y + h), radius=28, fill=(0, 0, 0, 150))
    draw.rounded_rectangle((x, y, x + w, y + h), radius=28, outline=fill, width=2)
    draw.text((x + 23, y + 12), text, font=font, fill=fill)


def pollinations_image(prompt, output_path, seed):
    final_prompt = f"""
warm 2D cartoon storybook illustration, cute expressive animal character, soft colors,
cinematic emotional lighting, clean composition, family friendly, vertical 9:16,
no text, no watermark, no logo. Scene: {prompt}
"""
    encoded = quote_plus(final_prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=1080&height=1920&seed={seed}&nologo=true&enhance=true&model=flux"
    response = requests.get(url, timeout=150)
    response.raise_for_status()
    output_path.write_bytes(response.content)
    img = Image.open(output_path)
    img.verify()
    return output_path


def fallback_background(output_path):
    img = Image.new("RGB", (WIDTH, HEIGHT), "#15202b")
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        r = int(18 + ratio * 25)
        g = int(30 + ratio * 30)
        b = int(45 + ratio * 35)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))
    img.save(output_path, quality=95)
    return output_path


def prepare_background(path):
    try:
        img = Image.open(path).convert("RGB")
        img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)
    except Exception:
        img = Image.open(fallback_background(FRAMES_DIR / "fallback_bg.jpg")).convert("RGB")
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 30))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def make_frame(video_id, scene_index, scene, title, image_path, total_scenes, hook_text, comment_prompt):
    bg = prepare_background(image_path).convert("RGBA")
    draw = ImageDraw.Draw(bg)

    top_gradient = Image.new("RGBA", (WIDTH, 330), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(top_gradient)
    for y in range(330):
        alpha = int(165 * (1 - y / 330))
        gdraw.line([(0, y), (WIDTH, y)], fill=(0, 0, 0, alpha))
    bg.alpha_composite(top_gradient, (0, 0))

    bottom_gradient = Image.new("RGBA", (WIDTH, 520), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(bottom_gradient)
    for y in range(520):
        alpha = int(185 * (y / 520))
        bdraw.line([(0, y), (WIDTH, y)], fill=(0, 0, 0, alpha))
    bg.alpha_composite(bottom_gradient, (0, HEIGHT - 520))

    brand_font = load_font(42, True)
    title_font = load_font(31, False)
    hook_font = load_font(54, True)
    subtitle_font = load_font(58, True)
    small_font = load_font(28, False)
    progress_font = load_font(24, True)

    draw.text((55, 36), "Tiny Brave Tails", font=brand_font, fill=(255, 238, 190, 255))
    title_lines = wrap_ltr(draw, title, title_font, 940, max_lines=2)
    title_y = 98
    for line in title_lines:
        draw.text((55, title_y), line, font=title_font, fill=(245, 245, 245, 235))
        title_y += 38

    if scene_index == 1:
        hook_lines = wrap_ltr(draw, hook_text, hook_font, 900, max_lines=2)
        draw_centered_lines(draw, hook_lines, hook_font, 350, fill=(255, 238, 190, 255), spacing=10)

    en_text = scene.get("subtitle_en", scene.get("narration_en", "")).strip()
    en_lines = wrap_ltr(draw, en_text, subtitle_font, 970, max_lines=3)
    draw_centered_lines(draw, en_lines, subtitle_font, HEIGHT - 285, fill=(255, 255, 255, 255), spacing=12)

    if scene_index == total_scenes:
        draw_badge(draw, comment_prompt, 55, HEIGHT - 505, small_font, fill=(255, 238, 190, 255))

    bar_x = 110
    bar_y = HEIGHT - 92
    bar_w = 860
    bar_h = 14
    progress = scene_index / total_scenes
    draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), radius=10, fill=(255, 255, 255, 70))
    draw.rounded_rectangle((bar_x, bar_y, bar_x + int(bar_w * progress), bar_y + bar_h), radius=10, fill=(255, 238, 190, 245))
    scene_label = f"{scene_index}/{total_scenes}"
    draw.text((bar_x + bar_w + 22, bar_y - 10), scene_label, font=progress_font, fill=(255, 255, 255, 210))

    frame_path = FRAMES_DIR / f"frame_{safe_filename(video_id)}_{scene_index:02d}.jpg"
    bg.convert("RGB").save(frame_path, quality=95)
    return frame_path


async def create_edge_audio_async(text, output_path, emotion):
    emotion_key = str(emotion or "emotional").lower()
    voice, pitch, rate = VOICE_BY_EMOTION.get(emotion_key, VOICE_BY_EMOTION["emotional"])
    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
    await communicate.save(str(output_path))


def create_edge_audio(text, output_path, emotion):
    asyncio.run(create_edge_audio_async(text, output_path, emotion))
    return output_path


def create_gtts_audio(text, output_path):
    clean = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    tts = gTTS(text=clean, lang="en", slow=False, tld="com")
    tts.save(str(output_path))
    return output_path


def create_espeak_audio(text, output_path):
    clean = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    command = ["espeak-ng", "-v", "en-us", "-s", "142", "-p", "48", "-a", "175", "-w", str(output_path), clean]
    subprocess.run(command, check=True)
    return output_path


def create_scene_audio(scene, video_id, scene_index):
    narration = scene.get("narration_en", "").strip()
    if not narration:
        raise ValueError(f"Missing narration_en for scene {scene_index}")
    safe_id = safe_filename(video_id)
    edge_path = AUDIO_DIR / f"audio_{safe_id}_{scene_index:02d}.mp3"
    gtts_path = AUDIO_DIR / f"audio_{safe_id}_{scene_index:02d}_gtts.mp3"
    wav_path = AUDIO_DIR / f"audio_{safe_id}_{scene_index:02d}.wav"
    emotion = scene.get("emotion", "emotional")

    try:
        create_edge_audio(narration, edge_path, emotion)
        return edge_path, "edge-tts"
    except Exception as e:
        print(f"edge-tts failed for scene {scene_index}: {e}")
    try:
        create_gtts_audio(narration, gtts_path)
        return gtts_path, "gTTS"
    except Exception as e:
        print(f"gTTS failed for scene {scene_index}: {e}")
        create_espeak_audio(narration, wav_path)
        return wav_path, "espeak-ng"


def make_motion_clip(frame_path, duration, scene_index):
    base = ImageClip(str(frame_path)).set_duration(duration)
    zoom_start = 1.0
    zoom_end = 1.055 if scene_index % 2 else 1.04

    def zoom(t):
        ratio = min(max(t / max(duration, 0.01), 0), 1)
        eased = 0.5 - 0.5 * math.cos(math.pi * ratio)
        return zoom_start + (zoom_end - zoom_start) * eased

    clip = base.resize(zoom)
    clip = clip.crop(x_center=WIDTH / 2, y_center=HEIGHT / 2, width=WIDTH, height=HEIGHT)
    clip = clip.fx(vfx.fadein, 0.12).fx(vfx.fadeout, 0.12)
    return clip


def create_video(video_id, title, scene_payload):
    scenes = scene_payload["scenes"]
    character = scene_payload.get("character", {})
    char_desc = character.get("description", "")
    hook_text = scene_payload.get("hook_text", "Wait for it")
    comment_prompt = scene_payload.get("comment_prompt", "What would you do?")

    safe_id = safe_filename(video_id)
    video_path = OUTPUT_DIR / f"tiny_brave_tails_{safe_id}.mp4"
    clips = []
    voice_sources = []
    total_scenes = len(scenes)

    for i, scene in enumerate(scenes, start=1):
        prompt = scene.get("image_prompt", "")
        full_prompt = f"{char_desc}. {prompt}"
        visual_path = VISUALS_DIR / f"visual_{safe_id}_{i:02d}.jpg"
        try:
            seed_base = int(re.sub(r"\D", "", safe_id) or random.randint(1000, 9999))
            pollinations_image(full_prompt, visual_path, seed=seed_base * 100 + i)
            time.sleep(0.8)
        except Exception as e:
            print(f"Pollinations failed for scene {i}: {e}")
            fallback_background(visual_path)

        audio_path, voice_source = create_scene_audio(scene, safe_id, i)
        voice_sources.append(voice_source)
        audio_clip = AudioFileClip(str(audio_path))
        duration = max(3.1, audio_clip.duration + 0.55)

        frame_path = make_frame(
            video_id=safe_id,
            scene_index=i,
            scene=scene,
            title=title,
            image_path=visual_path,
            total_scenes=total_scenes,
            hook_text=hook_text,
            comment_prompt=comment_prompt,
        )
        clip = make_motion_clip(frame_path, duration, i).set_audio(audio_clip.set_start(0))
        clips.append(clip)

    video = concatenate_videoclips(clips, method="compose", padding=0)
    video.write_videofile(
        str(video_path),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="medium",
        threads=2,
        bitrate="5200k",
    )
    video.close()
    for clip in clips:
        clip.close()
    return video_path, ",".join(sorted(set(voice_sources)))


def main():
    client = get_sheets_client()
    spreadsheet = client.open_by_key(SHEET_ID)
    content_sheet = spreadsheet.worksheet(CONTENT_SHEET_NAME)
    logs_sheet = spreadsheet.worksheet(LOGS_SHEET_NAME)

    values = content_sheet.get_all_values()
    if not values:
        raise ValueError("Content sheet is empty.")
    headers = values[0]

    id_col = find_column(headers, "id")
    title_col = find_column(headers, "title")
    status_col = find_column(headers, "status")
    scene_prompts_col = find_column(headers, "scene_prompts")
    image_status_col = find_column(headers, "image_status")
    audio_status_col = find_column(headers, "audio_status")
    video_file_path_col = find_optional_column(headers, "video_file_path")
    error_message_col = find_optional_column(headers, "error_message")

    target_row_number = None
    target_row = None
    for index, row in enumerate(values[1:], start=2):
        if get_cell(row, status_col).upper() == "GENERATED":
            target_row_number = index
            target_row = row
            break

    if target_row_number is None:
        log(logs_sheet, "", "GENERATE_VIDEO", "No GENERATED row found.")
        print("No GENERATED row found.")
        return

    video_id = get_cell(target_row, id_col)
    title = get_cell(target_row, title_col)
    scene_raw = get_cell(target_row, scene_prompts_col)
    if not title or not scene_raw:
        raise ValueError("Missing title or scene_prompts.")

    try:
        scene_payload = json.loads(scene_raw)
        video_path, voice_source = create_video(video_id, title, scene_payload)
        content_sheet.update_cell(target_row_number, status_col, "VIDEO_CREATED")
        content_sheet.update_cell(target_row_number, image_status_col, "CREATED")
        content_sheet.update_cell(target_row_number, audio_status_col, voice_source)
        update_optional(content_sheet, target_row_number, video_file_path_col, str(video_path))
        update_optional(content_sheet, target_row_number, error_message_col, "")
        log(logs_sheet, video_id, "GENERATE_VIDEO", f"Created English-only retention video: {video_path}. Voice: {voice_source}")
        print(f"Video created: {video_path}")
        print(f"Voice source: {voice_source}")
    except Exception as exc:
        update_optional(content_sheet, target_row_number, error_message_col, str(exc)[:500])
        log(logs_sheet, video_id, "FAILED_VIDEO", str(exc)[:1000])
        raise


if __name__ == "__main__":
    main()
