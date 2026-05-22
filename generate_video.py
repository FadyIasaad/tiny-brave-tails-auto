import os
import json
import re
import time
import asyncio
import subprocess
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests
import gspread
import edge_tts
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhancer
from moviepy.editor import (
    ImageClip, AudioFileClip, concatenate_videoclips,
    CompositeVideoClip, vfx, afx
)
from google.oauth2.service_account import Credentials

import arabic_reshaper
from bidi.algorithm import get_display


# ====================== CONFIGURATION ======================
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

EDGE_VOICE = "en-US-AriaNeural"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ====================== GOOGLE SHEETS ======================
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


def get_cell(row, col):
    return row[col - 1].strip() if len(row) >= col else ""


def log(logs_sheet, video_id, action, message):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    logs_sheet.append_row(
        [now, video_id, action, message],
        value_input_option="USER_ENTERED",
    )


# ====================== FONTS ======================
def ensure_fonts():
    """Download Arabic fonts if not available"""
    font_urls = {
        "NotoSansArabic-Bold.ttf": "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSansArabic/NotoSansArabic-Bold.ttf",
        "NotoSansArabic-Regular.ttf": "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSansArabic/NotoSansArabic-Regular.ttf",
    }
    
    fonts_dir = OUTPUT_DIR / "fonts"
    fonts_dir.mkdir(exist_ok=True)
    
    fonts = {}
    for font_name, url in font_urls.items():
        font_path = fonts_dir / font_name
        if not font_path.exists():
            print(f"Downloading {font_name}...")
            try:
                response = requests.get(url, timeout=30)
                font_path.write_bytes(response.content)
                print(f"Downloaded {font_name}")
            except Exception as e:
                print(f"Failed to download {font_name}: {e}")
        if font_path.exists():
            fonts[font_name] = str(font_path)
    
    return fonts


ARABIC_FONTS = ensure_fonts()


def load_latin_font(size, bold=True):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def load_arabic_font(size, bold=True):
    # Try downloaded fonts first
    if bold and "NotoSansArabic-Bold.ttf" in ARABIC_FONTS:
        try:
            return ImageFont.truetype(ARABIC_FONTS["NotoSansArabic-Bold.ttf"], size)
        except:
            pass
    if "NotoSansArabic-Regular.ttf" in ARABIC_FONTS:
        try:
            return ImageFont.truetype(ARABIC_FONTS["NotoSansArabic-Regular.ttf"], size)
        except:
            pass
    
    # System fonts
    paths = [
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
    ]
    for path in paths:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except:
                pass
    
    # Fallback to Latin font (won't render Arabic correctly but won't crash)
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
    except:
        return ImageFont.load_default()


# ====================== ARABIC TEXT PROCESSING ======================
def reshape_arabic_for_display(text):
    """Properly reshape Arabic text for display"""
    text = text.strip()
    if not text:
        return ""
    try:
        reshaped = arabic_reshaper.reshape(text)
        bidi_text = get_display(reshaped)
        return bidi_text
    except Exception as e:
        print(f"Arabic reshape error: {e}")
        return text


def wrap_ltr(draw, text, font, max_width):
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


def wrap_arabic(draw, text, font, max_width):
    """Wrap Arabic text handling RTL properly"""
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
    if current:
        logical_lines.append(current)
    return [reshape_arabic_for_display(line) for line in logical_lines]


def text_height(draw, lines, font, spacing):
    if not lines:
        return 0
    heights = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        heights.append(bbox[3] - bbox[1])
    return sum(heights) + spacing * max(0, len(lines) - 1)


def fit_ltr_text(draw, text, max_width, max_height, start_size=48, min_size=28):
    size = start_size
    while size >= min_size:
        font = load_latin_font(size, True)
        lines = wrap_ltr(draw, text, font, max_width)
        h = text_height(draw, lines, font, spacing=8)
        if h <= max_height:
            return font, lines
        size -= 2
    font = load_latin_font(min_size, True)
    lines = wrap_ltr(draw, text, font, max_width)
    return font, lines


def fit_arabic_text(draw, text, max_width, max_height, start_size=42, min_size=26):
    size = start_size
    while size >= min_size:
        font = load_arabic_font(size, True)
        lines = wrap_arabic(draw, text, font, max_width)
        h = text_height(draw, lines, font, spacing=8)
        if h <= max_height:
            return font, lines
        size -= 2
    font = load_arabic_font(min_size, True)
    lines = wrap_arabic(draw, text, font, max_width)
    return font, lines


def draw_centered_lines(draw, lines, font, center_y, fill, spacing=8):
    total_h = text_height(draw, lines, font, spacing)
    y = center_y - total_h // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        x = (WIDTH - w) // 2
        # Shadow for depth
        draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0, 200))
        draw.text((x, y), line, font=font, fill=fill)
        y += h + spacing


# ====================== IMAGE GENERATION ======================
def pollinations_image(prompt, output_path, seed):
    final_prompt = f"""
    warm 2D cartoon storybook illustration, cute expressive animal character, 
    soft pastel colors, gentle emotional lighting, family friendly, 
    vertical 9:16, no text, no watermark.
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
    """Create a beautiful gradient fallback background"""
    img = Image.new("RGB", (WIDTH, HEIGHT), "#0f1419")
    draw = ImageDraw.Draw(img)
    # Gradient
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        r = int(15 + ratio * 20)
        g = int(20 + ratio * 25)
        b = int(25 + ratio * 30)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))
    # Subtle stars
    for i in range(40):
        x = (i * 97) % WIDTH
        y = (i * 73) % HEIGHT
        draw.ellipse([x-2, y-2, x+2, y+2], fill=(255, 255, 255, 20))
    img.save(output_path, quality=95)
    return output_path


def prepare_background(path):
    """Load and prepare background image"""
    try:
        img = Image.open(path).convert("RGB")
        img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)
    except Exception as e:
        print(f"Failed to load image: {e}")
        fb_path = FRAMES_DIR / "fallback_bg.jpg"
        fallback_background(fb_path)
        img = Image.open(fb_path).convert("RGB")
    
    # Darken for better text contrast
    enhancer = ImageEnhance.Brightness(img)
    img = enhancer.enhance(0.8)
    
    # Overlay
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 45))
    bg = img.convert("RGBA")
    img = Image.alpha_composite(bg, overlay).convert("RGB")
    return img


# ====================== FRAME CREATION ======================
def make_frame(video_id, scene_index, scene, title, image_path, total_scenes):
    bg = prepare_background(image_path).convert("RGBA")
    
    # Top branding area with gradient
    top_overlay = Image.new("RGBA", (WIDTH, 250), (0, 0, 0, 130))
    bg.alpha_composite(top_overlay, (0, 0))
    
    # Subtitle area - soft dark box
    subtitle_h = 550
    subtitle_y = HEIGHT - subtitle_h - 90
    subtitle_box = Image.new("RGBA", (WIDTH, subtitle_h), (0, 0, 0, 180))
    subtitle_box = subtitle_box.filter(ImageFilter.GaussianBlur(3))
    bg.alpha_composite(subtitle_box, (0, subtitle_y))
    
    draw = ImageDraw.Draw(bg)
    
    brand_font = load_latin_font(46, True)
    title_font = load_latin_font(34, False)
    small_font = load_latin_font(28, False)
    
    # Brand name (golden)
    draw.text(
        (65, 45),
        "Tiny Brave Tails",
        font=brand_font,
        fill=(255, 238, 190, 255),
    )
    
    # Title (white)
    title_lines = wrap_ltr(draw, title, title_font, 940)[:2]
    title_y = 115
    for line in title_lines:
        draw.text((65, title_y), line, font=title_font, fill=(245, 245, 245, 240))
        title_y += 42
    
    # Get subtitle text
    en_text = scene.get("subtitle_en", scene.get("narration_en", "")).strip()
    ar_text = scene.get("subtitle_ar", "").strip() or scene.get("subtitle_ar", "").strip()
    
    # Calculate positions
    en_area_h = 195
    ar_area_h = 220
    
    en_font, en_lines = fit_ltr_text(
        draw, en_text, max_width=930, max_height=en_area_h, start_size=54, min_size=34
    )
    
    ar_font, ar_lines = fit_arabic_text(
        draw, ar_text, max_width=930, max_height=ar_area_h, start_size=48, min_size=32
    )
    
    # Draw English text (white, top subtitle area)
    draw_centered_lines(
        draw, en_lines, en_font, subtitle_y + 150, fill=(255, 255, 255, 255), spacing=10
    )
    
    # Draw Arabic text (golden, bottom subtitle area)
    draw_centered_lines(
        draw, ar_lines, ar_font, subtitle_y + 360, fill=(255, 220, 130, 255), spacing=10
    )
    
    # Progress bar
    bar_x = 130
    bar_y = HEIGHT - 105
    bar_w = 820
    bar_h = 16
    progress = scene_index / total_scenes
    
    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + bar_w, bar_y + bar_h),
        radius=12, fill=(255, 255, 255, 50)
    )
    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + int(bar_w * progress), bar_y + bar_h),
        radius=12, fill=(255, 210, 80, 255)
    )
    
    # Call to action
    cta = "Follow for tiny stories with big lessons"
    bbox = draw.textbbox((0, 0), cta, font=small_font)
    draw.text(((WIDTH - (bbox[2] - bbox[0])) // 2, HEIGHT - 62), cta, font=small_font, fill=(255, 255, 255, 200))
    
    # Save frame
    frame_path = FRAMES_DIR / f"frame_{video_id}_{scene_index:02d}.jpg"
    bg.convert("RGB").save(frame_path, quality=95)
    return frame_path


# ====================== AUDIO ======================
def convert_audio_to_wav(input_path, wav_path):
    command = [
        "ffmpeg", "-y", "-i", str(input_path), "-ar", "44100", "-ac", "2", str(wav_path)
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return wav_path


async def create_edge_audio_async(text, output_path):
    clean = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    communicate = edge_tts.Communicate(
        text=clean, voice=EDGE_VOICE, rate="-5%", volume="+0%", pitch="+0Hz"
    )
    await communicate.save(str(output_path))


def create_edge_audio(text, mp3_path, wav_path):
    asyncio.run(create_edge_audio_async(text, mp3_path))
    convert_audio_to_wav(mp3_path, wav_path)
    return wav_path


def create_gtts_audio(text, mp3_path, wav_path):
    clean = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    tts = gTTS(text=clean, lang="en", slow=False, tld="com")
    tts.save(str(mp3_path))
    convert_audio_to_wav(mp3_path, wav_path)
    return wav_path


def create_espeak_audio(text, output_path):
    clean = re.sub(r"\s+",
