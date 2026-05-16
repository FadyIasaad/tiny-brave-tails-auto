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
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
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

OUTPUT_DIR.mkdir(exist_ok=True)
FRAMES_DIR.mkdir(parents=True, exist_ok=True)
VISUALS_DIR.mkdir(parents=True, exist_ok=True)

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


def load_font(size, bold=True):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    for p in paths:
        if Path(p).exists():
            return ImageFont.truetype(p, size)

    return ImageFont.load_default()


def fix_arabic(text):
    if not text:
        return ""
    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped)


def wrap_text(draw, text, font, max_width):
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

    if current:
        lines.append(current)

    return lines


def draw_centered_lines(draw, lines, font, center_y, fill, shadow=True, spacing=12):
    heights = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        heights.append(bbox[3] - bbox[1])

    total_h = sum(heights) + spacing * (len(lines) - 1)
    y = center_y - total_h // 2

    for line, h in zip(lines, heights):
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        x = (WIDTH - w) // 2

        if shadow:
            draw.text((x + 4, y + 4), line, font=font, fill=(0, 0, 0, 220))
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

    response = requests.get(url, timeout=120)
    response.raise_for_status()

    output_path.write_bytes(response.content)

    try:
        img = Image.open(output_path)
        img.verify()
    except Exception:
        raise ValueError("Downloaded image is not valid.")

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
        img = ImageOps.exif_transpose(img)
    except Exception:
        return Image.open(fallback_background(FRAMES_DIR / "fallback_bg.jpg")).convert("RGB")

    img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 45))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def make_frame(video_id, scene_index, scene, title, image_path, total_scenes):
    bg = prepare_background(image_path).convert("RGBA")
    draw = ImageDraw.Draw(bg)

    brand_font = load_font(42, True)
    title_font = load_font(30, False)
    en_font = load_font(54, True)
    ar_font = load_font(42, True)
    small_font = load_font(30, False)

    top = Image.new("RGBA", (WIDTH, 190), (0, 0, 0, 105))
    bg.alpha_composite(top, (0, 0))

    draw.text((55, 48), "Tiny Brave Tails", font=brand_font, fill=(255, 238, 190, 255))
    draw.text((55, 112), title[:48], font=title_font, fill=(240, 240, 240, 225))

    # Bottom subtitle box
    box_h = 360
    subtitle_box = Image.new("RGBA", (WIDTH, box_h), (0, 0, 0, 150))
    subtitle_box = subtitle_box.filter(ImageFilter.GaussianBlur(1))
    bg.alpha_composite(subtitle_box, (0, HEIGHT - box_h - 80))

    draw = ImageDraw.Draw(bg)

    en_text = scene.get("en_subtitle", "").strip()
    ar_text = fix_arabic(scene.get("ar_subtitle", "").strip())

    en_lines = wrap_text(draw, en_text, en_font, 920)
    ar_lines = wrap_text(draw, ar_text, ar_font, 920)

    draw_centered_lines(
        draw,
        en_lines[:2],
        en_font,
        HEIGHT - 330,
        fill=(255, 255, 255, 255),
        spacing=10,
    )

    draw_centered_lines(
        draw,
        ar_lines[:2],
        ar_font,
        HEIGHT - 205,
        fill=(255, 232, 170, 255),
        spacing=8,
    )

    # Progress bar
    bar_x = 120
    bar_y = HEIGHT - 95
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


def create_voice_with_gtts(script, output_audio):
    clean_script = re.sub(r"\s+", " ", script.replace("\n", " ")).strip()
    tts = gTTS(text=clean_script, lang="en", slow=False, tld="com")
    tts.save(str(output_audio))


def create_voice_with_espeak(script, output_audio):
    clean_script = re.sub(r"\s+", " ", script.replace("\n", " ")).strip()
    command = [
        "espeak-ng",
        "-v", "en-us",
        "-s", "145",
        "-p", "45",
        "-a", "170",
        "-w", str(output_audio),
        clean_script,
    ]
    subprocess.run(command, check=True)


def create_voice(script, video_id):
    mp3_path = OUTPUT_DIR / f"voice_{video_id}.mp3"
    wav_path = OUTPUT_DIR / f"voice_{video_id}.wav"

    try:
        create_voice_with_gtts(script, mp3_path)
        return mp3_path, "gTTS"
    except Exception as e:
        print(f"gTTS failed. Fallback to espeak-ng. Error: {e}")
        create_voice_with_espeak(script, wav_path)
        return wav_path, "espeak-ng"


def create_video(video_id, title, script, scene_payload):
    scenes = scene_payload["scenes"]
    character = scene_payload.get("character", {})
    char_desc = character.get("description", "")

    safe_id = str(video_id).strip() or "video"
    video_path = OUTPUT_DIR / f"tiny_brave_tails_{safe_id}.mp4"

    audio_path, voice_source = create_voice(script, safe_id)
    audio_clip = AudioFileClip(str(audio_path))
    audio_duration = audio_clip.duration

    visual_paths = []

    for i, scene in enumerate(scenes, start=1):
        prompt = scene.get("image_prompt", "")
        full_prompt = f"{char_desc}. {prompt}"

        visual_path = VISUALS_DIR / f"visual_{safe_id}_{i:02d}.jpg"

        try:
            pollinations_image(full_prompt, visual_path, seed=int(safe_id) * 100 + i)
            time.sleep(1)
        except Exception as e:
            print(f"Pollinations failed for scene {i}: {e}")
            fallback_background(visual_path)

        visual_paths.append(visual_path)

    total_scenes = len(scenes)
    scene_duration = max(3.5, audio_duration / total_scenes)

    clips = []

    for i, scene in enumerate(scenes, start=1):
        frame_path = make_frame(
            video_id=safe_id,
            scene_index=i,
            scene=scene,
            title=title,
            image_path=visual_paths[i - 1],
            total_scenes=total_scenes,
        )

        clip = ImageClip(str(frame_path)).set_duration(scene_duration)
        clips.append(clip)

    video = concatenate_videoclips(clips, method="compose")
    video = video.set_audio(audio_clip)

    if video.duration > audio_duration:
        video = video.subclip(0, audio_duration)

    video.write_videofile(
        str(video_path),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="medium",
        threads=2,
        bitrate="4500k",
    )

    audio_clip.close()
    video.close()

    return video_path, voice_source


def main():
    client = get_sheets_client()
    spreadsheet = client.open_by_key(SHEET_ID)

    content_sheet = spreadsheet.worksheet(CONTENT_SHEET_NAME)
    logs_sheet = spreadsheet.worksheet(LOGS_SHEET_NAME)

    values = content_sheet.get_all_values()
    headers = values[0]

    id_col = find_column(headers, "id")
    script_col = find_column(headers, "script")
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
    script = get_cell(target_row, script_col)
    title = get_cell(target_row, title_col)
    scene_raw = get_cell(target_row, scene_prompts_col)

    if not script or not title or not scene_raw:
        raise ValueError("Missing script/title/scene_prompts.")

    scene_payload = json.loads(scene_raw)

    video_path, voice_source = create_video(
        video_id=video_id,
        title=title,
        script=script,
        scene_payload=scene_payload,
    )

    content_sheet.update_cell(target_row_number, status_col, "VIDEO_CREATED")
    content_sheet.update_cell(target_row_number, image_status_col, "CREATED")
    content_sheet.update_cell(target_row_number, audio_status_col, voice_source)

    log(
        logs_sheet,
        video_id,
        "GENERATE_VIDEO",
        f"Created 2D storybook bilingual video: {video_path}. Voice: {voice_source}",
    )

    print(f"Video created: {video_path}")
    print(f"Voice source: {voice_source}")


if __name__ == "__main__":
    main()
