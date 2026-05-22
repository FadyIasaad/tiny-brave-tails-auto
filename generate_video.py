<think>
The user wants me to review their code and fix the Arabic translation problem while also making the video more elegant and attractive. Let me analyze their code:

1. **Arabic Support**: They already have:
   - `arabic_reshaper` and `bidi.algorithm` imported
   - `reshape_arabic_for_display()` function
   - `wrap_arabic()` function
   - `fit_arabic_text()` function
   - `load_arabic_font()` function

   The problem might be:
   - Font files not found on the system
   - Arabic text might not be properly reshaped before display

2. **Video Elegance**: The current video uses:
   - Basic ImageClips
   - Simple overlays
   - No animations or transitions

Let me identify the issues:

1. **Arabic Problem**: The code looks correct, but the issue could be:
   - The font files don't exist on the system
   - The text needs to be properly processed before rendering
   
2. **Elegance Improvements**: I can add:
   - Ken Burns effect (slow zoom/pan) for images
   - Smooth fade transitions between scenes
   - Better visual effects
   - Background music
   - Improved typography

Let me rewrite the code with fixes and improvements:

1. Fix Arabic text rendering
2. Add Ken Burns effect (zoom/pan)
3. Add transitions (fade in/out)
4. Add background music support
5. Better visual effects
6. More professional text rendering
</think>

# Fixed & Improved Version

I've analyzed your code and identified the issues. Here's the **fixed and enhanced version**:

## Key Fixes:

| Issue | Fix |
|-------|-----|
| Arabic showing as boxes | Added font download + fallback handling |
| No animations | Added Ken Burns effect (slow zoom/pan) |
| Basic transitions | Added fade in/out between scenes |
| No background music | Added royalty-free music support |
| Plain visuals | Enhanced with better gradients & blur effects |

---

## Updated Code

```python
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


# ============== CONFIGURATION ==============
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

# Add background music (royalty-free)
BGM_URL = "https://cdn.pixabay.com/download/audio/2022/10/25/audio_8466b858eb.mp3"
BGM_PATH = OUTPUT_DIR / "background_music.mp3"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# ============== GOOGLE SHEETS ==============
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


# ============== FONTS (FIXED FOR ARABIC) ==============
def download_fonts():
    """Download Arabic fonts if not available"""
    font_urls = {
        "NotoSansArabic-Bold.ttf": "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSansArabic/NotoSansArabic-Bold.ttf",
        "NotoSansArabic-Regular.ttf": "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSansArabic/NotoSansArabic-Regular.ttf",
        "NotoNaskhArabic-Bold.ttf": "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoNaskhArabic/NotoNaskhArabic-Bold.ttf",
    }
    
    fonts_dir = OUTPUT_DIR / "fonts"
    fonts_dir.mkdir(exist_ok=True)
    
    downloaded = {}
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
                continue
        
        if font_path.exists():
            downloaded[font_name] = str(font_path)
    
    return downloaded


# Pre-download fonts
ARABIC_FONTS = download_fonts()


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
    # First try downloaded fonts
    if bold and "NotoSansArabic-Bold.ttf" in ARABIC_FONTS:
        return ImageFont.truetype(ARABIC_FONTS["NotoSansArabic-Bold.ttf"], size)
    elif "NotoSansArabic-Regular.ttf" in ARABIC_FONTS:
        return ImageFont.truetype(ARABIC_FONTS["NotoSansArabic-Regular.ttf"], size)
    
    if bold and "NotoNaskhArabic-Bold.ttf" in ARABIC_FONTS:
        return ImageFont.truetype(ARABIC_FONTS["NotoNaskhArabic-Bold.ttf"], size)

    # Then system fonts
    paths = [
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
    ]

    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(path, size)

    # Fallback to DejaVu (won't render Arabic properly but won't crash)
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
    except:
        return ImageFont.load_default()


# ============== ARABIC TEXT PROCESSING (FIXED) ==============
def reshape_arabic_for_display(text):
    """Properly reshape Arabic text for display"""
    text = text.strip()
    if not text:
        return ""
    
    try:
        # Reshape Arabic letters
        reshaped = arabic_reshaper.reshape(text)
        # Handle bidirectional text
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
    """Wrap Arabic text handling RTL"""
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

    # Reshape each line for display
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

        # Shadow for better readability
        draw.text((x + 4, y + 4), line, font=font, fill=(0, 0, 0, 235))
        draw.text((x, y), line, font=font, fill=fill)

        y += h + spacing


# ============== IMAGE GENERATION ==============
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

    # Create vertical gradient
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        # Dark blue to slightly lighter gradient
        r = int(15 + ratio * 20)
        g = int(20 + ratio * 25)
        b = int(25 + ratio * 30)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))

    # Add subtle pattern
    for i in range(30):
        x = int(HEIGHT * 0.05) + i * 60
        y = (i * 73) % HEIGHT
        draw.ellipse([x-2, y-2, x+2, y+2], fill=(255, 255, 255, 15))

    img.save(output_path, quality=95)
    return output_path


def prepare_background(path):
    """Load and prepare background image with overlay"""
    try:
        img = Image.open(path).convert("RGB")
        img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)
    except Exception:
        img = Image.open(
            fallback_background(FRAMES_DIR / "fallback_bg.jpg")
        ).convert("RGB")

    # Darken slightly for better text contrast
    enhancer = ImageEnhance.Brightness(img)
    img = enhancer.factor(0.85)

    # Add overlay
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 40))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    return img


# ============== FRAME CREATION (IMPROVED) ==============
def make_frame(video_id, scene_index, scene, title, image_path, total_scenes):
    bg = prepare_background(image_path).convert("RGBA")

    # Top gradient overlay (branding area)
    top_overlay = Image.new("RGBA", (WIDTH, 240), (0, 0, 0, 120))
    bg.alpha_composite(top_overlay, (0, 0))

    # Subtitle area - soft blurry dark box
    subtitle_h = 540
    subtitle_y = HEIGHT - subtitle_h - 80
    subtitle_box = Image.new("RGBA", (WIDTH, subtitle_h), (0, 0, 0, 175))
    subtitle_box = subtitle_box.filter(ImageFilter.GaussianBlur(2))
    bg.alpha_composite(subtitle_box, (0, subtitle_y))

    draw = ImageDraw.Draw(bg)

    brand_font = load_latin_font(44, True)
    title_font = load_latin_font(32, False)
    small_font = load_latin_font(26, False)

    # Brand name
    draw.text(
        (60, 42),
        "Tiny Brave Tails",
        font=brand_font,
        fill=(255, 238, 190, 255),
    )

    # Title
    title_lines = wrap_ltr(draw, title, title_font, 950)[:2]
    title_y = 108

    for line in title_lines:
        draw.text(
            (60, title_y),
            line,
            font=title_font,
            fill=(240, 240, 240, 235),
        )
        title_y += 40

    # Get text
    en_text = scene.get("subtitle_en", scene.get("narration_en", "")).strip()
    ar_text = scene.get("subtitle_ar", "").strip()

    # Calculate text positions
    en_area_h = 190
    ar_area_h = 215

    en_font, en_lines = fit_ltr_text(
        draw,
        en_text,
        max_width=940,
        max_height=en_area_h,
        start_size=52,
        min_size=32,
    )

    ar_font, ar_lines = fit_arabic_text(
        draw,
        ar_text,
        max_width=940,
        max_height=ar_area_h,
        start_size=46,
        min_size=30,
    )

    # Draw English text (white)
    draw_centered_lines(
        draw,
        en_lines,
        en_font,
        subtitle_y + 145,
        fill=(255, 255, 255, 255),
        spacing=10,
    )

    # Draw Arabic text (golden)
    draw_centered_lines(
        draw,
        ar_lines,
        ar_font,
        subtitle_y + 350,
        fill=(255, 225, 150, 255),
        spacing=10,
    )

    # Progress bar
    bar_x = 120
    bar_y = HEIGHT - 100
    bar_w = 840
    bar_h = 14
    progress = scene_index / total_scenes

    # Background track
    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + bar_w, bar_y + bar_h),
        radius=10,
        fill=(255, 255, 255, 55),
    )

    # Progress fill
    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + int(bar_w * progress), bar_y + bar_h),
        radius=10,
        fill=(255, 210, 100, 255),
    )

    # Call to action
    cta = "Follow for tiny stories with big lessons"
    bbox = draw.textbbox((0, 0), cta, font=small_font)

    draw.text(
        ((WIDTH
