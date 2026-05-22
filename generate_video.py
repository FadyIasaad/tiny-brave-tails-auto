import os
import json
import re
import time
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests
import gspread
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips
from google.oauth2.service_account import Credentials

import arabic_reshaper
from bidi.algorithm import get_display


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


def get_cell(row, col):
    return row[col - 1].strip() if len(row) >= col else ""


def log(logs_sheet, video_id, action, message):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    logs_sheet.append_row([now, video_id, action, message], value_input_option="USER_ENTERED")


def load_latin_font(size, bold=True):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def load_arabic_font(size, bold=True):
    paths = [
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def reshape_arabic_for_display(text):
    text = text.strip()
    if not text:
        return ""
    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped)


def wrap_ltr(draw, text, font, max_width, max_lines=3):
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


def wrap_arabic(draw, text, font, max_width, max_lines=3):
    words = text.split()
    logical_lines = []
    current = ""

    for word in words:
        test_logical = (current + " " + word).strip()
        test_visual = reshape_arabic_for_display(test_logical)
        bbox = draw.textbbox((0, 0), test_visual, font=font)

        if bbox[2] - bbox[0] <= max_width:
            current = test_logical
        else:
            if current:
                logical_lines.append(current)
            current = word

        if len(logical_lines) >= max_lines:
            break

    if current and len(logical_lines) < max_lines:
        logical_lines.append(current)

    return [reshape_arabic_for_display(line) for line in logical_lines[:max_lines]]


def draw_centered_lines(draw, lines, font, center_y, fill, spacing=8):
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

        draw.text((x + 4, y + 4), line, font=font, fill=(0, 0, 0, 230))
        draw.text((x, y), line, font=font, fill=fill)

        y += h + spacing


def pollinations_image(prompt, output_path, seed):
    final_prompt = f"""
warm 2D cartoon storybook illustration, cute expressive animal character, soft colors,
gentle emotional lighting, family friendly, vertical 9:16, no text, no watermark.
Scene: {prompt}
"""
    encoded = quote_plus(final_prompt)
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1080&height=1920&seed={seed}&nologo=true&enhance=true"
    )

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

    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 35))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def make_frame(video_id, scene_index, scene, title, image_path, total_scenes):
    bg = prepare_background(image_path).convert("RGBA")

    top_overlay = Image.new("RGBA", (WIDTH, 220), (0, 0, 0, 100))
    bg.alpha_composite(top_overlay, (0, 0))

    subtitle_h = 450
    subtitle_y = HEIGHT - subtitle_h - 75
    subtitle_box = Image.new("RGBA", (WIDTH, subtitle_h), (0, 0, 0, 160))
    subtitle_box = subtitle_box.filter(ImageFilter.GaussianBlur(1))
    bg.alpha_composite(subtitle_box, (0, subtitle_y))

    draw = ImageDraw.Draw(bg)

    brand_font = load_latin_font(42, True)
    title_font = load_latin_font(30, False)
    en_font = load_latin_font(46, True)
    ar_font = load_arabic_font(42, True)
    small_font = load_latin_font(28, False)

    draw.text((55, 38), "Tiny Brave Tails", font=brand_font, fill=(255, 238, 190, 255))

    title_lines = wrap_ltr(draw, title, title_font, 950, max_lines=2)
    title_y = 102
    for line in title_lines:
        draw.text((55, title_y), line, font=title_font, fill=(240, 240, 240, 230))
        title_y += 38

    en_text = scene.get("subtitle_en", scene.get("narration_en", "")).strip()
    ar_text = scene.get("subtitle_ar", "").strip()

    en_lines = wrap_ltr(draw, en_text, en_font, 940, max_lines=3)
    ar_lines = wrap_arabic(draw, ar_text, ar_font, 940, max_lines=3)

    draw_centered_lines(
        draw,
        en_lines,
        en_font,
        subtitle_y + 135,
        fill=(255, 255, 255, 255),
        spacing=8,
    )

    draw_centered_lines(
        draw,
        ar_lines,
        ar_font,
        subtitle_y + 305,
        fill=(255, 232, 170, 255),
        spacing=8,
    )

    bar_x = 120
    bar_y = HEIGHT - 92
    bar_w = 840
    bar_h = 12
    progress = scene_index / total_scenes

    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + bar_w, bar_y + bar_h),
        radius=8,
        fill=(255, 255, 255, 65),
    )
    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + int(bar_w * progress), bar_y + bar_h),
        radius=8,
        fill=(255, 232, 170, 245),
    )

    cta = "Follow for tiny stories with big lessons"
    bbox = draw.textbbox((0, 0), cta, font=small_font)
    draw.text(
        ((WIDTH - (bbox[2] - bbox[0])) // 2, HEIGHT - 58),
        cta,
        font=small_font,
        fill=(255, 255, 255, 220),
    )

    frame_path = FRAMES_DIR / f"frame_{video_id}_{scene_index:02d}.jpg"
    bg.convert("RGB").save(frame_path, quality=95)
    return frame_path


def create_gtts_audio(text, output_path):
    clean = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    tts = gTTS(text=clean, lang="en", slow=False, tld="com")
    tts.save(str(output_path))
    return output_path


def create_espeak_audio(text, output_path):
    clean = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    command = [
        "espeak-ng",
        "-v", "en-us",
        "-s", "145",
        "-p", "45",
        "-a", "170",
        "-w", str(output_path),
        clean,
    ]
    subprocess.run(command, check=True)
    return output_path


def create_scene_audio(scene, video_id, scene_index):
    narration = scene.get("narration_en", "").strip()
    if not narration:
        raise ValueError(f"Missing narration_en for scene {scene_index}")

    mp3_path = AUDIO_DIR / f"audio_{video_id}_{scene_index:02d}.mp3"
    wav_path = AUDIO_DIR / f"audio_{video_id}_{scene_index:02d}.wav"

    try:
        create_gtts_audio(narration, mp3_path)
        return mp3_path, "gTTS"
    except Exception as e:
        print(f"gTTS failed for scene {scene_index}: {e}")
        create_espeak_audio(narration, wav_path)
        return wav_path, "espeak-ng"


def create_video(video_id, title, scene_payload):
    scenes = scene_payload["scenes"]
    character = scene_payload.get("character", {})
    char_desc = character.get("description", "")

    safe_id = str(video_id).strip() or "video"
    video_path = OUTPUT_DIR / f"tiny_brave_tails_{safe_id}.mp4"

    clips = []
    voice_sources = []
    total_scenes = len(scenes)

    for i, scene in enumerate(scenes, start=1):
        prompt = scene.get("image_prompt", "")
        full_prompt = f"{char_desc}. {prompt}"

        visual_path = VISUALS_DIR / f"visual_{safe_id}_{i:02d}.jpg"

        try:
            pollinations_image(full_prompt, visual_path, seed=int(safe_id) * 100 + i)
            time.sleep(0.8)
        except Exception as e:
            print(f"Pollinations failed for scene {i}: {e}")
            fallback_background(visual_path)

        audio_path, voice_source = create_scene_audio(scene, safe_id, i)
        voice_sources.append(voice_source)

        audio_clip = AudioFileClip(str(audio_path))
        duration = max(3.0, audio_clip.duration + 0.25)

        frame_path = make_frame(
            video_id=safe_id,
            scene_index=i,
            scene=scene,
            title=title,
            image_path=visual_path,
            total_scenes=total_scenes,
        )

        clip = ImageClip(str(frame_path)).set_duration(duration).set_audio(audio_clip)
        clips.append(clip)

    video = concatenate_videoclips(clips, method="compose")

    video.write_videofile(
        str(video_path),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="medium",
        threads=2,
        bitrate="4500k",
    )

    video.close()

    return video_path, ",".join(sorted(set(voice_sources)))


def main():
    client = get_sheets_client()
    spreadsheet = client.open_by_key(SHEET_ID)

    content_sheet = spreadsheet.worksheet(CONTENT_SHEET_NAME)
    logs_sheet = spreadsheet.worksheet(LOGS_SHEET_NAME)

    values = content_sheet.get_all_values()
    headers = values[0]

    id_col = find_column(headers, "id")
    title_col = find_column(headers, "title")
    status_col = find_column(headers, "status")
    scene_prompts_col = find_column(headers, "scene_prompts")
    image_status_col = find_column(headers, "image_status")
    audio_status_col = find_column(headers, "audio_status")

    target_row_number = None
    target_row = None

    for index, row in enumerate(values[1:], start=2):
        if get_cell(row, status_col) == "GENERATED":
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

    scene_payload = json.loads(scene_raw)

    video_path, voice_source = create_video(video_id, title, scene_payload)

    content_sheet.update_cell(target_row_number, status_col, "VIDEO_CREATED")
    content_sheet.update_cell(target_row_number, image_status_col, "CREATED")
    content_sheet.update_cell(target_row_number, audio_status_col, voice_source)

    log(
        logs_sheet,
        video_id,
        "GENERATE_VIDEO",
        f"Created fixed Arabic bilingual 2D storybook video: {video_path}. Voice: {voice_source}",
    )

    print(f"Video created: {video_path}")
    print(f"Voice source: {voice_source}")


if __name__ == "__main__":
    main()
