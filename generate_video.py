import os
import json
import re
import asyncio
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import gspread
import edge_tts
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips
from google.oauth2.service_account import Credentials


SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

CONTENT_SHEET_NAME = "Content"
LOGS_SHEET_NAME = "Logs"

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

WIDTH = 1080
HEIGHT = 1920
FPS = 24

VOICE = "en-US-GuyNeural"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_sheets_client():
    service_account_info = json.loads(SERVICE_ACCOUNT_JSON)
    credentials = Credentials.from_service_account_info(
        service_account_info,
        scopes=SCOPES,
    )
    return gspread.authorize(credentials)


def find_column(headers, name):
    if name not in headers:
        raise ValueError(f"Missing required column: {name}")
    return headers.index(name) + 1


def log(logs_sheet, video_id, action, message):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    logs_sheet.append_row(
        [now, video_id, action, message],
        value_input_option="USER_ENTERED",
    )


def get_cell(row, col):
    return row[col - 1].strip() if len(row) >= col else ""


def split_script(script):
    script = script.replace("\n", " ").strip()
    parts = re.split(r"(?<=[.!?])\s+", script)
    parts = [p.strip() for p in parts if p.strip()]

    chunks = []
    current = ""

    for part in parts:
        if len(current) + len(part) <= 120:
            current = (current + " " + part).strip()
        else:
            if current:
                chunks.append(current)
            current = part

    if current:
        chunks.append(current)

    if not chunks:
        chunks = [script]

    return chunks


def load_font(size):
    possible_fonts = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    for font_path in possible_fonts:
        if Path(font_path).exists():
            return ImageFont.truetype(font_path, size)

    return ImageFont.load_default()


def make_gradient_background():
    image = Image.new("RGB", (WIDTH, HEIGHT), "#101820")
    draw = ImageDraw.Draw(image)

    for y in range(HEIGHT):
        ratio = y / HEIGHT
        r = int(12 + ratio * 18)
        g = int(22 + ratio * 28)
        b = int(34 + ratio * 38)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))

    # soft light circles
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.ellipse((-250, 150, 520, 920), fill=(255, 210, 130, 35))
    odraw.ellipse((620, 850, 1350, 1600), fill=(80, 180, 255, 28))
    overlay = overlay.filter(ImageFilter.GaussianBlur(90))

    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def draw_centered_multiline(draw, text, font, y, fill, max_width, line_spacing=18):
    words = text.split()
    lines = []
    line = ""

    for word in words:
        test_line = (line + " " + word).strip()
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] <= max_width:
            line = test_line
        else:
            if line:
                lines.append(line)
            line = word

    if line:
        lines.append(line)

    line_heights = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_heights.append(bbox[3] - bbox[1])

    total_height = sum(line_heights) + line_spacing * (len(lines) - 1)
    current_y = y - total_height // 2

    for line, line_height in zip(lines, line_heights):
        bbox = draw.textbbox((0, 0), line, font=font)
        line_width = bbox[2] - bbox[0]
        x = (WIDTH - line_width) // 2

        # shadow
        draw.text((x + 4, current_y + 4), line, font=font, fill=(0, 0, 0, 160))
        draw.text((x, current_y), line, font=font, fill=fill)

        current_y += line_height + line_spacing


def create_frame(text, title, video_id, segment_index, total_segments):
    img = make_gradient_background().convert("RGBA")
    draw = ImageDraw.Draw(img)

    title_font = load_font(48)
    body_font = load_font(68)
    small_font = load_font(34)

    # top brand
    brand = "Tiny Brave Tails"
    draw.text((60, 70), brand, font=title_font, fill=(255, 235, 190, 255))

    # small story title
    wrapped_title = textwrap.shorten(title, width=34, placeholder="...")
    draw.text((60, 145), wrapped_title, font=small_font, fill=(220, 230, 240, 230))

    # simple animal paw / circle visual
    cx, cy = WIDTH // 2, 500
    draw.ellipse((cx - 150, cy - 150, cx + 150, cy + 150), fill=(255, 235, 190, 35), outline=(255, 235, 190, 110), width=4)
    draw.ellipse((cx - 45, cy - 20, cx + 45, cy + 70), fill=(255, 235, 190, 180))
    draw.ellipse((cx - 105, cy - 85, cx - 55, cy - 35), fill=(255, 235, 190, 170))
    draw.ellipse((cx + 55, cy - 85, cx + 105, cy - 35), fill=(255, 235, 190, 170))
    draw.ellipse((cx - 35, cy - 125, cx + 35, cy - 55), fill=(255, 235, 190, 170))

    # main caption
    draw_centered_multiline(
        draw=draw,
        text=text,
        font=body_font,
        y=980,
        fill=(255, 255, 255, 255),
        max_width=900,
        line_spacing=20,
    )

    # progress bar
    bar_x = 120
    bar_y = 1700
    bar_w = 840
    bar_h = 12
    progress = (segment_index + 1) / total_segments

    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + bar_w, bar_y + bar_h),
        radius=8,
        fill=(255, 255, 255, 55),
    )
    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + int(bar_w * progress), bar_y + bar_h),
        radius=8,
        fill=(255, 235, 190, 230),
    )

    # bottom CTA
    cta = "Follow for tiny stories with big lessons"
    bbox = draw.textbbox((0, 0), cta, font=small_font)
    draw.text(
        ((WIDTH - (bbox[2] - bbox[0])) // 2, 1760),
        cta,
        font=small_font,
        fill=(230, 235, 240, 220),
    )

    frame_path = OUTPUT_DIR / f"frame_{video_id}_{segment_index:02d}.png"
    img.convert("RGB").save(frame_path, quality=95)
    return frame_path


async def create_voice(script, output_audio):
    communicate = edge_tts.Communicate(script, VOICE)
    await communicate.save(str(output_audio))


def create_video(video_id, title, script):
    safe_id = str(video_id).strip() or "video"
    audio_path = OUTPUT_DIR / f"voice_{safe_id}.mp3"
    video_path = OUTPUT_DIR / f"tiny_brave_tails_{safe_id}.mp4"

    asyncio.run(create_voice(script, audio_path))

    audio_clip = AudioFileClip(str(audio_path))
    audio_duration = audio_clip.duration

    chunks = split_script(script)
    total_segments = len(chunks)

    # distribute time by text length
    total_chars = sum(len(chunk) for chunk in chunks)
    clips = []

    for i, chunk in enumerate(chunks):
        if total_chars > 0:
            duration = max(2.2, audio_duration * (len(chunk) / total_chars))
        else:
            duration = audio_duration / total_segments

        frame_path = create_frame(chunk, title, safe_id, i, total_segments)
        clip = ImageClip(str(frame_path)).set_duration(duration)
        clips.append(clip)

    video = concatenate_videoclips(clips, method="compose")
    video = video.set_audio(audio_clip)

    video.write_videofile(
        str(video_path),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="medium",
        threads=2,
        bitrate="4000k",
    )

    audio_clip.close()
    video.close()

    return video_path


def main():
    client = get_sheets_client()
    spreadsheet = client.open_by_key(SHEET_ID)

    content_sheet = spreadsheet.worksheet(CONTENT_SHEET_NAME)
    logs_sheet = spreadsheet.worksheet(LOGS_SHEET_NAME)

    all_values = content_sheet.get_all_values()

    if not all_values:
        raise ValueError("Content sheet is empty.")

    headers = all_values[0]

    id_col = find_column(headers, "id")
    script_col = find_column(headers, "script")
    title_col = find_column(headers, "title")
    status_col = find_column(headers, "status")

    target_row_number = None
    target_row = None

    for index, row in enumerate(all_values[1:], start=2):
        status = get_cell(row, status_col)

        if status == "GENERATED":
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

    if not script or not title:
        raise ValueError(f"Missing script/title in row {target_row_number}")

    video_path = create_video(video_id, title, script)

    content_sheet.update_cell(target_row_number, status_col, "VIDEO_CREATED")

    log(
        logs_sheet,
        video_id,
        "GENERATE_VIDEO",
        f"Video created for row {target_row_number}: {video_path}",
    )

    print(f"Video created: {video_path}")


if __name__ == "__main__":
    main()
