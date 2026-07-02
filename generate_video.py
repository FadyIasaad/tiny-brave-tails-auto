import asyncio
import json
import math
import os
import random
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed as futures_as_completed
from pathlib import Path
from urllib.parse import quote_plus

import edge_tts
import requests
from moviepy.editor import AudioFileClip, CompositeVideoClip, ImageClip, VideoFileClip, concatenate_videoclips
from moviepy.video.fx.fadein import fadein
from moviepy.video.fx.fadeout import fadeout
from PIL import Image, ImageDraw, ImageFilter, ImageFont

if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.LANCZOS

from config import (
    AMBIENT_BED_VOLUME,
    BRAND_STING_VOLUME,
    CHANNEL_NAME,
    DEFAULT_AMBIENT_BED_VOLUME,
    ENABLE_AMBIENT_BED,
    ENABLE_BRAND_STING,
    LOUDNESS_TARGET_LUFS,
    THUMBNAIL_DIR,
    ENABLE_TRANSITIONS,
    TRANSITION_SECONDS,
    ENABLE_WATERMARK,
    WATERMARK_TEXT,
    WATERMARK_OPACITY,
    ENABLE_CHAPTERS,
    CHAPTERS_MIN_SCENES,
    ENABLE_END_SCREEN,
    END_SCREEN_SECONDS,
)
from nd_common import (
    find_optional_column,
    find_column,
    get_all_values,
    get_cell,
    get_sheets_client,
    get_worksheet,
    get_logs_worksheet,
    log,
    open_spreadsheet,
    update_cell,
    update_optional,
)

CONTENT_SHEET_NAME = "Content"
LOGS_SHEET_NAME = "Logs"
OUTPUT_DIR = Path("output")
FRAMES_DIR = OUTPUT_DIR / "frames"
VISUALS_DIR = OUTPUT_DIR / "visuals"
AUDIO_DIR = OUTPUT_DIR / "audio"
VIDEO_DIR = OUTPUT_DIR / "videos"
THUMB_DIR = THUMBNAIL_DIR
for folder in [OUTPUT_DIR, FRAMES_DIR, VISUALS_DIR, AUDIO_DIR, VIDEO_DIR, THUMB_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

WIDTH = 1080
HEIGHT = 1920
FPS = 24
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "").strip()
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "").strip()
USE_STOCK_FIRST = os.getenv("USE_STOCK_FIRST", "false").lower() in {"1", "true", "yes"}

# ─── EMOTION-DRIVEN VOICE SYSTEM ─────────────────────────────────────────────
# One narrator voice for the whole channel, with calibrated rate / pitch / volume
# per emotion so quiet dread, sharp fear, and matter-of-fact confession all feel
# distinct without ever sounding like a different person.
EMOTION_STYLE = {
    "dread":        {"voice": "en-US-AriaNeural", "rate": "-18%", "pitch": "-3Hz", "volume": "+0%"},
    "tension":      {"voice": "en-US-AriaNeural", "rate": "-10%", "pitch": "-1Hz", "volume": "+2%"},
    "eerie":        {"voice": "en-US-AriaNeural", "rate": "-16%", "pitch": "-4Hz", "volume": "-2%"},
    "calm":         {"voice": "en-US-AriaNeural", "rate": "-20%", "pitch": "-2Hz", "volume": "-3%"},
    "fear":         {"voice": "en-US-AriaNeural", "rate": "-8%",  "pitch": "-2Hz", "volume": "+3%"},
    "relief":       {"voice": "en-US-AriaNeural", "rate": "-14%", "pitch": "+1Hz", "volume": "+0%"},
    "mystery":      {"voice": "en-US-AriaNeural", "rate": "-14%", "pitch": "-2Hz", "volume": "+0%"},
    "anger":        {"voice": "en-US-AriaNeural", "rate": "-6%",  "pitch": "-1Hz", "volume": "+4%"},
    "satisfaction": {"voice": "en-US-AriaNeural", "rate": "-12%", "pitch": "+0Hz", "volume": "+1%"},
}

# Inter-sentence pause per emotion (used in SSML <break> tags)
EMOTION_PAUSE = {
    "dread":        "700ms",
    "tension":      "350ms",
    "eerie":        "650ms",
    "calm":         "750ms",
    "fear":         "300ms",
    "relief":       "500ms",
    "mystery":      "550ms",
    "anger":        "280ms",
    "satisfaction": "450ms",
}

SEARCH_WORDS = {
    "dread":        "empty house night cinematic dark",
    "tension":      "dark hallway suspense cinematic",
    "eerie":        "foggy forest night eerie cinematic",
    "calm":         "rain window night quiet cinematic",
    "fear":         "dark figure shadow cinematic night",
    "relief":       "warm light window night cinematic",
    "mystery":      "dark room mystery cinematic",
    "anger":        "storm dark intense cinematic",
    "satisfaction": "quiet sunrise calm cinematic",
}

# ─── FONT HELPERS ─────────────────────────────────────────────────────────────
def load_font(size, bold=True):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"     if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

# ─── STOCK VISUAL HELPERS ────────────────────────────────────────────────────
def safe_query(text, emotion):
    raw = f"{text} {SEARCH_WORDS.get(str(emotion).lower(), 'dark cinematic night')}"
    raw = re.sub(r"[^A-Za-z0-9 ]+", " ", raw)
    words = [w for w in raw.split() if len(w) > 2]
    banned = {"shot", "wide", "close", "vertical", "text", "watermark", "cinematic", "illustration", "narrator"}
    words = [w for w in words if w.lower() not in banned]
    return " ".join(words[:10]) or SEARCH_WORDS.get(str(emotion).lower(), "dark cinematic night")

def download_file(url, path, headers=None):
    r = requests.get(url, headers=headers or {}, timeout=90, stream=True)
    r.raise_for_status()
    path.write_bytes(r.content)
    return path

def pexels_video(query, output_path):
    if not PEXELS_API_KEY:
        raise RuntimeError("PEXELS_API_KEY is missing")
    r = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": PEXELS_API_KEY},
        params={"query": query, "orientation": "portrait", "per_page": 8, "size": "medium"},
        timeout=60,
    )
    r.raise_for_status()
    videos = r.json().get("videos", [])
    for video in videos:
        files = sorted(video.get("video_files", []), key=lambda x: abs((x.get("width") or 0) - WIDTH) + abs((x.get("height") or 0) - HEIGHT))
        for f in files:
            link = f.get("link")
            if link and (f.get("height") or 0) >= 720:
                return download_file(link, output_path)
    raise RuntimeError("No usable Pexels video")

def pexels_photo(query, output_path):
    if not PEXELS_API_KEY:
        raise RuntimeError("PEXELS_API_KEY is missing")
    r = requests.get(
        "https://api.pexels.com/v1/search",
        headers={"Authorization": PEXELS_API_KEY},
        params={"query": query, "orientation": "portrait", "per_page": 10},
        timeout=60,
    )
    r.raise_for_status()
    photos = r.json().get("photos", [])
    for photo in photos:
        src = photo.get("src", {})
        link = src.get("portrait") or src.get("large2x") or src.get("large")
        if link:
            return download_file(link, output_path)
    raise RuntimeError("No usable Pexels photo")

def pixabay_video(query, output_path):
    if not PIXABAY_API_KEY:
        raise RuntimeError("PIXABAY_API_KEY is missing")
    r = requests.get(
        "https://pixabay.com/api/videos/",
        params={"key": PIXABAY_API_KEY, "q": query, "per_page": 10, "safesearch": "true", "video_type": "film"},
        timeout=60,
    )
    r.raise_for_status()
    hits = r.json().get("hits", [])
    for hit in hits:
        vids = hit.get("videos", {})
        for key in ["large", "medium", "small"]:
            link = vids.get(key, {}).get("url")
            if link:
                return download_file(link, output_path)
    raise RuntimeError("No usable Pixabay video")

def pixabay_photo(query, output_path):
    if not PIXABAY_API_KEY:
        raise RuntimeError("PIXABAY_API_KEY is missing")
    r = requests.get(
        "https://pixabay.com/api/",
        params={"key": PIXABAY_API_KEY, "q": query, "image_type": "photo", "orientation": "vertical", "per_page": 10, "safesearch": "true"},
        timeout=60,
    )
    r.raise_for_status()
    hits = r.json().get("hits", [])
    for hit in hits:
        link = hit.get("largeImageURL") or hit.get("webformatURL")
        if link:
            return download_file(link, output_path)
    raise RuntimeError("No usable Pixabay photo")

def fetch_stock_visual(shot, safe_id, index):
    emotion = shot.get("emotion", "calm")
    query = safe_query(f"{shot.get('image_prompt','')} {shot.get('narration_en','')}", emotion)
    attempts = [
        ("pexels_video",  pexels_video,  VISUALS_DIR / f"visual_{safe_id}_{index:03d}.mp4"),
        ("pixabay_video", pixabay_video, VISUALS_DIR / f"visual_{safe_id}_{index:03d}.mp4"),
        ("pexels_photo",  pexels_photo,  VISUALS_DIR / f"visual_{safe_id}_{index:03d}.jpg"),
        ("pixabay_photo", pixabay_photo, VISUALS_DIR / f"visual_{safe_id}_{index:03d}.jpg"),
    ]
    errors = []
    for name, func, path in attempts:
        try:
            func(query, path)
            return path, name, query
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    raise RuntimeError("Stock visual failed. Add valid PEXELS_API_KEY and PIXABAY_API_KEY. " + " | ".join(errors[:4]))


# ─── AI CINEMATIC IMAGE (dark, moody Nightfall Diaries look) ─────────────────
_POLLINATIONS_MAX_CONCURRENCY = int(os.getenv("ND_POLLINATIONS_CONCURRENCY", "1"))
_POLLINATIONS_SEMAPHORE = threading.Semaphore(_POLLINATIONS_MAX_CONCURRENCY)
_POLLINATIONS_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_POLLINATIONS_TOKEN = os.getenv("POLLINATIONS_TOKEN", "").strip()
# Respect Pollinations rate tiers: anonymous = 1 req/15s, Seed (free token) = 1
# req/5s. Space requests globally and send the token so we stop tripping 429s.
_POLLINATIONS_MIN_INTERVAL = float(
    os.getenv("ND_POLLINATIONS_MIN_INTERVAL", "5.5" if _POLLINATIONS_TOKEN else "16")
)
_POLLINATIONS_RATE_LOCK = threading.Lock()
_POLLINATIONS_LAST_TS = [0.0]


def _pollinations_headers():
    return {"Authorization": f"Bearer {_POLLINATIONS_TOKEN}"} if _POLLINATIONS_TOKEN else {}


def _pollinations_wait_turn():
    """Block until at least _POLLINATIONS_MIN_INTERVAL has passed since the last
    request, so concurrent shots don't burst past the provider's rate tier."""
    with _POLLINATIONS_RATE_LOCK:
        wait = _POLLINATIONS_MIN_INTERVAL - (time.monotonic() - _POLLINATIONS_LAST_TS[0])
        if wait > 0:
            time.sleep(wait)
        _POLLINATIONS_LAST_TS[0] = time.monotonic()


def _status_code_of(exc):
    resp = getattr(exc, "response", None)
    return getattr(resp, "status_code", None)


def pollinations_cinematic_image(prompt, output_path, seed, max_attempts=6):
    """
    Generates a dark, moody cinematic still via Pollinations.ai, matching the
    Nightfall Diaries aesthetic: restrained, atmospheric, low-light.

    Robust against rate limiting: limits concurrent requests via a global
    semaphore and retries each image with exponential backoff on 429 / transient
    server errors so a momentary rate limit no longer fails the whole render.
    """
    style_prefix = (
        "semi-realistic dark animation, atmospheric illustrated artwork, "
        "moody late-night scene, deep cinematic shadows, rich saturated dark tones, "
        "digital painting style, detailed background environment, "
        "anime-inspired semi-realistic illustration, dramatic lighting, "
        "faces stylized or partially hidden, no graphic gore, "
        "no text, no watermark, no logo, vertical 9:16 aspect ratio. "
        "Scene: "
    )
    full_prompt = style_prefix + str(prompt)
    encoded = quote_plus(full_prompt)
    urls = [
        f"https://image.pollinations.ai/prompt/{encoded}?width={WIDTH}&height={HEIGHT}&seed={seed}&nologo=true&enhance=true&model=flux-anime",
        f"https://image.pollinations.ai/prompt/{encoded}?width={WIDTH}&height={HEIGHT}&seed={seed}&nologo=true&enhance=true&model=flux",
        f"https://image.pollinations.ai/prompt/{encoded}?width={WIDTH}&height={HEIGHT}&seed={seed}&nologo=true&enhance=true",
    ]
    last_error = None
    for attempt in range(1, max_attempts + 1):
        saw_retryable = False
        for url in urls:
            try:
                with _POLLINATIONS_SEMAPHORE:
                    _pollinations_wait_turn()
                    r = requests.get(url, timeout=150, headers=_pollinations_headers())
                r.raise_for_status()
                output_path.write_bytes(r.content)
                with Image.open(output_path) as img:
                    img.verify()
                return output_path
            except Exception as exc:
                last_error = exc
                status = _status_code_of(exc)
                if status is None or status in _POLLINATIONS_RETRYABLE_STATUS:
                    saw_retryable = True
        if attempt < max_attempts and saw_retryable:
            wait = min(45.0, (2 ** attempt) + random.uniform(0, 3))
            print(f"[pollinations] attempt {attempt}/{max_attempts} rate-limited/failed "
                  f"({last_error}); retrying in {wait:.1f}s")
            time.sleep(wait)
        elif not saw_retryable:
            break
    raise RuntimeError(f"AI cinematic image failed after retries: {last_error}")


# ─── SUBTITLE / FRAME HELPERS ─────────────────────────────────────────────────
def wrap_ltr(draw, text, font, max_width, max_lines=3):
    words = str(text or "").split()
    lines, current = [], ""
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

def draw_centered_lines(draw, lines, font, center_y, fill, spacing=9):
    if not lines:
        return
    heights = [
        draw.textbbox((0, 0), line, font=font)[3] - draw.textbbox((0, 0), line, font=font)[1]
        for line in lines
    ]
    total_h = sum(heights) + spacing * max(0, len(lines) - 1)
    y = center_y - total_h // 2
    for line, h in zip(lines, heights):
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (WIDTH - (bbox[2] - bbox[0])) // 2
        for dx, dy in [(-3, -3), (3, -3), (-3, 3), (3, 3), (0, 4), (0, -4)]:
            draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 210))
        draw.text((x, y), line, font=font, fill=fill)
        y += h + spacing

def prepare_photo(path):
    """
    Prepare a visual frame. Darker overlays than a bright/warm channel would
    use, to keep the late-night Nightfall Diaries mood consistent.
    """
    img = Image.open(path).convert("RGB")
    ratio = max(WIDTH / img.width, HEIGHT / img.height)
    new_size = (int(img.width * ratio), int(img.height * ratio))
    img = img.resize(new_size, Image.LANCZOS)
    left = (img.width - WIDTH) // 2
    top  = (img.height - HEIGHT) // 2
    img = img.crop((left, top, left + WIDTH, top + HEIGHT)).convert("RGBA")
    # Header gradient (branding area)
    img.alpha_composite(Image.new("RGBA", (WIDTH, 170), (0, 0, 0, 90)), (0, 0))
    # Subtitle gradient at bottom
    img.alpha_composite(Image.new("RGBA", (WIDTH, 340), (0, 0, 0, 150)), (0, HEIGHT - 340))
    return img

def draw_watermark(draw):
    """
    Small persistent channel watermark in the bottom-right corner. Drawn on
    both photo frames and transparent caption overlays so every shot carries
    light branding (helps recognition and re-uploads). Controlled by config.
    """
    if not ENABLE_WATERMARK or not WATERMARK_TEXT:
        return
    wm_font = load_font(22, bold=True)
    bbox = draw.textbbox((0, 0), WATERMARK_TEXT, font=wm_font)
    tw = bbox[2] - bbox[0]
    x = WIDTH - tw - 40
    y = HEIGHT - 60
    # subtle shadow then semi-transparent text
    draw.text((x + 1, y + 1), WATERMARK_TEXT, font=wm_font, fill=(0, 0, 0, min(255, WATERMARK_OPACITY)))
    draw.text((x, y), WATERMARK_TEXT, font=wm_font, fill=(235, 235, 240, min(255, WATERMARK_OPACITY)))

def make_frame(video_id, shot_index, shot, title, image_path, total_shots):
    bg = prepare_photo(image_path)
    draw = ImageDraw.Draw(bg)

    brand_font = load_font(40, bold=True)
    title_font = load_font(26, bold=False)
    sub_font   = load_font(44, bold=True)

    # Brand name
    draw.text((50, 34), CHANNEL_NAME, font=brand_font, fill=(200, 210, 230, 255))
    # Episode title (2 lines max)
    y = 94
    for line in wrap_ltr(draw, title, title_font, 940, 2):
        draw.text((50, y), line, font=title_font, fill=(225, 225, 230, 220))
        y += 36

    subtitle = ""
    if os.getenv("SHOW_SUBTITLES", "true").lower() not in {"0", "false", "no"}:
        subtitle = (shot.get("subtitle_en") or shot.get("narration_en", "")).strip()

    draw_centered_lines(
        draw,
        wrap_ltr(draw, subtitle, sub_font, 950, 3),
        sub_font,
        HEIGHT - 210,
        (235, 235, 240, 255),
        spacing=10,
    )

    draw_watermark(draw)

    frame_path = FRAMES_DIR / f"frame_{video_id}_{shot_index:03d}.jpg"
    bg.convert("RGB").save(frame_path, quality=95)
    return frame_path


def make_subtitle_overlay(video_id, shot_index, shot, title):
    """
    Same caption styling as make_frame, but rendered onto a transparent layer
    instead of a background photo. Used to burn captions onto stock video
    clips, which previously had no subtitle text at all.
    """
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    brand_font = load_font(40, bold=True)
    title_font = load_font(26, bold=False)
    sub_font   = load_font(44, bold=True)

    # Soft gradient strips behind the brand/title area and the subtitle area
    # so captions stay readable over busy stock footage.
    overlay.alpha_composite(Image.new("RGBA", (WIDTH, 170), (0, 0, 0, 90)), (0, 0))
    overlay.alpha_composite(Image.new("RGBA", (WIDTH, 340), (0, 0, 0, 150)), (0, HEIGHT - 340))

    draw.text((50, 34), CHANNEL_NAME, font=brand_font, fill=(200, 210, 230, 255))
    y = 94
    for line in wrap_ltr(draw, title, title_font, 940, 2):
        draw.text((50, y), line, font=title_font, fill=(225, 225, 230, 220))
        y += 36

    subtitle = ""
    if os.getenv("SHOW_SUBTITLES", "true").lower() not in {"0", "false", "no"}:
        subtitle = (shot.get("subtitle_en") or shot.get("narration_en", "")).strip()

    draw_centered_lines(
        draw,
        wrap_ltr(draw, subtitle, sub_font, 950, 3),
        sub_font,
        HEIGHT - 210,
        (235, 235, 240, 255),
        spacing=10,
    )

    draw_watermark(draw)

    overlay_path = FRAMES_DIR / f"caption_{video_id}_{shot_index:03d}.png"
    overlay.save(overlay_path)
    return overlay_path


# ─── THUMBNAIL GENERATION ──────────────────────────────────────────────────────
def generate_thumbnail(video_id, title, image_path):
    """
    Builds a simple high-contrast custom thumbnail from one of the episode's
    own cinematic stills: dark gradient band, bold title text. No extra API
    calls or paid tools, just PIL on an image already generated for the video.
    """
    thumb_w, thumb_h = 1280, 720
    try:
        img = Image.open(image_path).convert("RGB")
    except Exception:
        img = Image.new("RGB", (thumb_w, thumb_h), (10, 10, 14))

    ratio = max(thumb_w / img.width, thumb_h / img.height)
    new_size = (int(img.width * ratio), int(img.height * ratio))
    img = img.resize(new_size, Image.LANCZOS)
    left = (img.width - thumb_w) // 2
    top = (img.height - thumb_h) // 2
    img = img.crop((left, top, left + thumb_w, top + thumb_h)).convert("RGBA")

    # Darken slightly overall, then a stronger gradient band behind the title
    # so bold text stays readable over any background.
    img.alpha_composite(Image.new("RGBA", (thumb_w, thumb_h), (0, 0, 0, 60)))
    band_h = 260
    img.alpha_composite(Image.new("RGBA", (thumb_w, band_h), (0, 0, 0, 175)), (0, thumb_h - band_h))

    draw = ImageDraw.Draw(img)
    title_font = load_font(72, bold=True)
    brand_font = load_font(34, bold=True)

    lines = wrap_ltr(draw, title, title_font, thumb_w - 100, max_lines=2)
    total_h = len(lines) * 84
    y = thumb_h - band_h + (band_h - total_h) // 2 - 10
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=title_font)
        x = (thumb_w - (bbox[2] - bbox[0])) // 2
        for dx, dy in [(-4, -4), (4, -4), (-4, 4), (4, 4)]:
            draw.text((x + dx, y + dy), line, font=title_font, fill=(0, 0, 0, 230))
        draw.text((x, y), line, font=title_font, fill=(245, 245, 250, 255))
        y += 84

    draw.text((40, 30), CHANNEL_NAME.upper(), font=brand_font, fill=(230, 200, 120, 255))

    thumb_path = THUMB_DIR / f"thumb_{video_id}.jpg"
    img.convert("RGB").save(thumb_path, quality=92)
    return thumb_path


def make_end_screen_frame(video_id, title):
    """
    A simple dark outro card for long-form videos: channel name, a 'watch
    another story' nudge, and space where YouTube's linked end-screen element
    will sit. Pure PIL, no extra API calls.
    """
    img = Image.new("RGBA", (WIDTH, HEIGHT), (8, 8, 12, 255))
    draw = ImageDraw.Draw(img)

    brand_font = load_font(58, bold=True)
    line_font = load_font(46, bold=True)
    sub_font = load_font(34, bold=False)

    draw.text((60, 220), CHANNEL_NAME, font=brand_font, fill=(225, 200, 120, 255))

    lines = [
        "If you made it this far,",
        "stay a little longer.",
        "",
        "Another story is waiting.",
    ]
    y = HEIGHT // 2 - 120
    for ln in lines:
        if not ln:
            y += 30
            continue
        bbox = draw.textbbox((0, 0), ln, font=line_font)
        x = (WIDTH - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), ln, font=line_font, fill=(235, 235, 240, 255))
        y += 70

    tip = "Tap the next video to keep the lights low."
    bbox = draw.textbbox((0, 0), tip, font=sub_font)
    draw.text(((WIDTH - (bbox[2] - bbox[0])) // 2, HEIGHT - 360), tip, font=sub_font, fill=(170, 175, 190, 255))

    draw_watermark(draw)

    outro_path = FRAMES_DIR / f"endscreen_{video_id}.jpg"
    img.convert("RGB").save(outro_path, quality=92)
    return outro_path


# ─── VOICE: HUMANIZE + SSML AUDIO ────────────────────────────────────────────
def humanize_text(text):
    clean = re.sub(r"\s+", " ", str(text or "").replace("\n", " ")).strip()
    if not clean:
        raise ValueError("Empty narration text")
    clean = re.sub(r"\.{4,}", "...", clean)
    return clean


def _build_ssml(text: str, emotion: str, style: dict) -> str:
    pause = EMOTION_PAUSE.get(emotion, "480ms")
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not sentences:
        sentences = [text]

    def esc(t):
        return (t.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;")
                 .replace('"', "&quot;"))

    body = "".join(f"<s>{esc(s)}</s><break time='{pause}'/>" for s in sentences)

    return (
        "<speak version='1.0' "
        "xmlns='http://www.w3.org/2001/10/synthesis' "
        "xml:lang='en-US'>"
        f"<voice name='{style['voice']}'>"
        f"<prosody rate='{style['rate']}' "
        f"pitch='{style['pitch']}' "
        f"volume='{style['volume']}'>"
        f"{body}"
        "</prosody></voice></speak>"
    )


async def create_edge_audio_async(text, output_path, emotion="calm"):
    """
    Generate audio with edge-tts using SSML for per-emotion prosody.
    Retries up to 3 times with exponential backoff on network errors.
    Does NOT fall back to espeak — if all attempts fail the exception propagates
    so the job fails loudly with a clear error instead of producing robotic audio.
    """
    style = EMOTION_STYLE.get(str(emotion).lower(), EMOTION_STYLE["calm"])
    voice = style["voice"]
    clean = humanize_text(text)
    last_error = None

    # IMPORTANT: edge-tts does NOT support hand-written SSML. If you pass an SSML
    # string it gets escaped and the voice literally reads the markup aloud (the
    # "it just reads the tags / system voice" bug). Use edge-tts's native
    # rate/pitch/volume parameters on PLAIN narration text instead — that applies
    # the per-emotion prosody correctly while speaking the actual story.
    for attempt in range(3):
        if attempt > 0:
            wait = 2 ** attempt  # 2s, 4s
            print(f"edge-tts attempt {attempt + 1}/3 after {wait}s delay (last error: {last_error})")
            await asyncio.sleep(wait)
        try:
            communicate = edge_tts.Communicate(
                text=clean,
                voice=voice,
                rate=style["rate"],
                pitch=style["pitch"],
                volume=style["volume"],
            )
            await communicate.save(str(output_path))
            print(f"edge-tts succeeded on attempt {attempt + 1}")
            return
        except Exception as exc:
            last_error = exc

    raise RuntimeError(
        f"edge-tts failed after 3 attempts for emotion='{emotion}', voice='{voice}'. "
        f"Last error: {last_error}. "
        "Check GitHub Actions network access to Microsoft TTS servers."
    )


def create_edge_audio(text, output_path, emotion="calm"):
    asyncio.run(create_edge_audio_async(text, output_path, emotion))
    return output_path

def create_espeak_audio(text, output_path):
    subprocess.run(
        ["espeak-ng", "-v", "en-us", "-s", "112", "-p", "30", "-a", "140",
         "-w", str(output_path), humanize_text(text)],
        check=True,
    )
    return output_path

def normalize_audio(input_path, video_id, shot_index):
    normalized = AUDIO_DIR / f"audio_{video_id}_{shot_index:03d}_norm.m4a"
    command = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,acompressor=threshold=-22dB:ratio=2.2:attack=20:release=250",
        "-ar", "48000", "-ac", "2", "-c:a", "aac", "-b:a", "192k",
        str(normalized),
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return normalized
    except Exception:
        return input_path

def create_shot_audio(shot, video_id, shot_index):
    """
    Generate narration audio for a single shot using edge-tts (neural voice).
    espeak-ng fallback is intentionally removed — robotic audio is not acceptable.
    If edge-tts fails, the job will fail with a clear error message.
    """
    narration = shot.get("narration_en", "").strip()
    emotion   = shot.get("emotion", "calm").strip().lower()
    mp3_path  = AUDIO_DIR / f"audio_{video_id}_{shot_index:03d}.mp3"
    create_edge_audio(narration, mp3_path, emotion)
    voice = EMOTION_STYLE.get(emotion, EMOTION_STYLE["calm"])["voice"]
    return normalize_audio(mp3_path, video_id, shot_index), f"edge-ssml:{voice}:{emotion}"


# ─── AMBIENT SOUND BED (generated, not sourced — zero copyright risk) ────────
def build_ambient_bed(duration_seconds, output_path):
    """
    Generates a quiet rain/drone ambient bed entirely with ffmpeg's built-in
    audio sources (anoisesrc + aevalsrc). Nothing is downloaded, so there is
    no licensing risk, and it never depends on a third-party music API.
    """
    duration = max(3.0, float(duration_seconds))
    fade_out_start = max(0.0, duration - 6.0)
    filter_complex = (
        "[0:a]lowpass=f=700,highpass=f=80,volume=0.5[rain];"
        "[1:a]volume=0.35[drone];"
        "[rain][drone]amix=inputs=2:duration=longest:normalize=0[bed];"
        f"[bed]afade=t=in:st=0:d=5,afade=t=out:st={fade_out_start:.2f}:d=6[out]"
    )
    command = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"anoisesrc=color=brown:amplitude=1:duration={duration:.2f}",
        "-f", "lavfi", "-i", f"aevalsrc=0.3*sin(2*PI*55*t):duration={duration:.2f}",
        "-filter_complex", filter_complex,
        "-map", "[out]", "-ac", "2", "-ar", "48000",
        str(output_path),
    ]
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return output_path


def build_brand_sting(output_path, duration=1.6):
    """
    Generates a short two-tone chime entirely with ffmpeg's built-in audio
    sources, the same zero-copyright-risk approach as the ambient bed. Mixed
    in at the very start of every video for channel recognition.
    """
    filter_complex = (
        "[0:a]volume=1.0,afade=t=in:st=0:d=0.05,afade=t=out:st=0.55:d=0.35[note1];"
        "[1:a]volume=0.8,afade=t=in:st=0:d=0.05,afade=t=out:st=0.85:d=0.45[note2];"
        "[note1][note2]concat=n=2:v=0:a=1[chime]"
    )
    command = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "aevalsrc=0.35*sin(2*PI*392*t):duration=0.65",
        "-f", "lavfi", "-i", "aevalsrc=0.30*sin(2*PI*523*t):duration=0.95",
        "-filter_complex", filter_complex,
        "-map", "[chime]", "-ac", "2", "-ar", "48000",
        str(output_path),
    ]
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return output_path


def add_ambient_bed(video_path: Path, ambient_volume: float = 0.10, sting_volume: float = 0.0) -> bool:
    """
    Mixes the generated ambient bed quietly under the video's existing
    narration track, and (if sting_volume > 0) overlays the brand sting at the
    very start. Wrapped so a failure here never breaks the whole render; the
    video is still perfectly usable without these layers.
    """
    bed_path = None
    sting_path = None
    mixed_path = None
    try:
        with VideoFileClip(str(video_path)) as probe:
            duration = probe.duration
        bed_path = video_path.with_name(video_path.stem + "_ambient_bed.wav")
        build_ambient_bed(duration, bed_path)
        mixed_path = video_path.with_name(video_path.stem + "_mixed.mp4")

        inputs = ["-i", str(video_path), "-i", str(bed_path)]
        if sting_volume > 0:
            sting_path = video_path.with_name(video_path.stem + "_sting.wav")
            build_brand_sting(sting_path)
            inputs += ["-i", str(sting_path)]
            filter_complex = (
                f"[1:a]volume={ambient_volume}[amb];"
                f"[2:a]volume={sting_volume}[sting];"
                "[0:a][amb][sting]amix=inputs=3:duration=first:normalize=0[aout]"
            )
        else:
            filter_complex = f"[1:a]volume={ambient_volume}[amb];[0:a][amb]amix=inputs=2:duration=first:normalize=0[aout]"

        command = [
            "ffmpeg", "-y", *inputs,
            "-filter_complex", filter_complex,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            str(mixed_path),
        ]
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        mixed_path.replace(video_path)
        return True
    except Exception as exc:
        print(f"Ambient bed / sting skipped (non-fatal): {exc}")
        return False
    finally:
        for p in (bed_path, sting_path, mixed_path):
            try:
                if p and p.exists():
                    p.unlink()
            except Exception:
                pass


def normalize_final_loudness(video_path: Path, target_lufs: float = -14.0) -> bool:
    """
    Final loudness pass over the fully mixed video (narration + ambient + sting
    already combined), so every upload lands at the same perceived loudness
    regardless of how the layers above summed. Non-fatal on failure.
    """
    normalized_path = video_path.with_name(video_path.stem + "_loudnorm.mp4")
    try:
        command = [
            "ffmpeg", "-y", "-i", str(video_path),
            "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            str(normalized_path),
        ]
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        normalized_path.replace(video_path)
        return True
    except Exception as exc:
        print(f"Final loudness normalization skipped (non-fatal): {exc}")
        return False
    finally:
        try:
            if normalized_path.exists():
                normalized_path.unlink()
        except Exception:
            pass


# ─── VIDEO CLIP HELPERS ───────────────────────────────────────────────────────
def motion_params(motion, duration):
    if motion == "slow_zoom_out":
        return lambda t: 1.09 - 0.055 * (t / max(duration, 0.1))
    return lambda t: 1.0 + 0.07 * (t / max(duration, 0.1))

def animated_photo_clip(frame_path, duration, motion):
    clip = ImageClip(str(frame_path)).set_duration(duration)
    zoom = motion_params(motion, duration)
    clip = clip.resize(lambda t: zoom(t))
    return clip.set_position(("center", "center")).on_color(
        size=(WIDTH, HEIGHT), color=(0, 0, 0), pos=("center", "center")
    )

def stock_video_clip(video_path, duration):
    clip = VideoFileClip(str(video_path)).without_audio()
    if clip.duration > duration:
        start = max(0, (clip.duration - duration) / 2)
        clip = clip.subclip(start, start + duration)
    else:
        clip = clip.loop(duration=duration)
    ratio = max(WIDTH / clip.w, HEIGHT / clip.h)
    clip = clip.resize(ratio)
    clip = clip.crop(x_center=clip.w / 2, y_center=clip.h / 2, width=WIDTH, height=HEIGHT)
    return clip.set_duration(duration)


# ─── STORY → FLAT SHOT LIST ───────────────────────────────────────────────────
def distribute_narration(narration, max_shots=4):
    """Split scene narration into at most max_shots contiguous, non-overlapping
    chunks so the full narration is spoken exactly ONCE across the shots. Stored
    per-shot narration often overlapped or restated the whole scene, which made
    the voice repeat and loop lines; distributing fixes that at render time."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", (narration or "").strip()) if s.strip()]
    if not sentences:
        text = (narration or "").strip()
        return [text] if text else [""]
    n = max(1, min(max_shots, len(sentences)))
    base, extra = divmod(len(sentences), n)
    chunks, idx = [], 0
    for g in range(n):
        take = base + (1 if g < extra else 0)
        chunks.append(" ".join(sentences[idx:idx + take]))
        idx += take
    return chunks


def split_scene_to_shots(scene):
    scene_narration = str(scene.get("narration_en", "") or "").strip()
    raw = scene.get("shots") if isinstance(scene.get("shots"), list) else []
    raw = raw[:4]
    motions = ["slow_zoom_in", "gentle_pan_left", "tiny_handheld", "slow_zoom_out"]
    if raw:
        # Distribute the scene narration across the stored shots with NO overlap so
        # the voice never repeats a line. Keep each shot's own image_prompt/visuals.
        chunks = distribute_narration(scene_narration, len(raw))
        out = []
        for i, shot in enumerate(raw):
            chunk = chunks[i] if i < len(chunks) else ""
            s = dict(shot)
            s["narration_en"] = chunk
            s["subtitle_en"] = chunk
            out.append(s)
        out = [s for s in out if str(s.get("narration_en", "")).strip()]
        return out or [dict(raw[0], narration_en=scene_narration, subtitle_en=scene_narration)]
    # No stored shots: derive up to 4 visual shots from the narration sentences.
    parts = [x.strip() for x in re.split(r"(?<=[.!?])\s+", scene_narration) if x.strip()]
    if not parts:
        parts = [scene_narration or "The room was quiet, in a way that felt deliberate."]
    parts = parts[:4]
    return [
        {
            "shot_number":  i + 1,
            "emotion":      scene.get("emotion", "calm"),
            "narration_en": part,
            "subtitle_en":  part,
            "image_prompt": f"{scene.get('image_prompt','')} {part}",
            "camera_motion": motions[i % 4],
            "pause_after":  0.25,
        }
        for i, part in enumerate(parts)
    ]

def flatten_story(scene_payload):
    shots = []
    for scene_index, scene in enumerate(scene_payload.get("scenes", []), start=1):
        scene_title = (scene.get("scene_title") or scene.get("title") or scene.get("chapter_title") or "").strip()
        for shot in split_scene_to_shots(scene):
            prompt = shot.get("image_prompt") or scene.get("image_prompt", "")
            shots.append({
                "scene_number": scene_index,
                "scene_index":  scene_index,
                "scene_title":  scene_title,
                "shot_number":  shot.get("shot_number", len(shots) + 1),
                "emotion":      shot.get("emotion", scene.get("emotion", "calm")),
                "narration_en": shot.get("narration_en") or scene.get("narration_en", ""),
                "subtitle_en":  shot.get("subtitle_en") or shot.get("narration_en") or scene.get("subtitle_en", ""),
                "image_prompt": prompt,
                "camera_motion": shot.get("camera_motion") or scene.get("camera_motion", "slow_zoom_in"),
                "pause_after":  shot.get("pause_after", 0.25),
            })
    return shots


# ─── VISUAL FETCH (AI primary, stock fallback) ────────────────────────────────
def make_placeholder_visual(shot, output_path, seed):
    """Atmospheric dark placeholder frame, generated locally with no network.

    Used when AI image generation is unavailable (e.g. Pollinations.ai rate
    limiting / 429s). This guarantees every shot has a valid full-size frame so
    a single failed image can never abort the whole render. Real AI images are
    still used whenever the provider cooperates.
    """
    rnd = random.Random(seed)
    top = (rnd.randint(8, 16), rnd.randint(8, 18), rnd.randint(16, 30))
    bottom = (max(0, top[0] - 6), max(0, top[1] - 6), max(0, top[2] - 10))
    # Build the vertical gradient as a 1px-wide column, then stretch to width
    # (fast: HEIGHT iterations instead of WIDTH*HEIGHT).
    column = Image.new("RGB", (1, HEIGHT))
    cpx = column.load()
    for y in range(HEIGHT):
        t = y / max(1, HEIGHT - 1)
        cpx[0, y] = (
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
        )
    base = column.resize((WIDTH, HEIGHT)).convert("RGB")
    # Soft atmospheric glow blob (distant light) for a less flat look.
    glow = Image.new("L", (WIDTH, HEIGHT), 0)
    gd = ImageDraw.Draw(glow)
    gx = rnd.randint(int(WIDTH * 0.25), int(WIDTH * 0.75))
    gy = rnd.randint(int(HEIGHT * 0.2), int(HEIGHT * 0.6))
    gr = rnd.randint(int(WIDTH * 0.25), int(WIDTH * 0.5))
    gd.ellipse([gx - gr, gy - gr, gx + gr, gy + gr], fill=55)
    glow = glow.filter(ImageFilter.GaussianBlur(gr * 0.6))
    tint = Image.new("RGB", (WIDTH, HEIGHT), (38, 42, 64))
    base = Image.composite(tint, base, glow)
    base = base.filter(ImageFilter.GaussianBlur(2))
    base.save(output_path, "JPEG", quality=88)
    return output_path


def fetch_visual(shot, safe_id, index, numeric_seed):
    """Generate a cinematic AI image for this shot (Pollinations.ai).

    Falls back to a locally-generated atmospheric placeholder if the AI provider
    is unavailable, so rate limiting degrades quality gracefully instead of
    failing the entire render.
    """
    prompt = (
        f"{shot.get('image_prompt','')} "
        f"Emotion: {shot.get('emotion','calm')}. "
        f"Moment: {shot.get('narration_en','')}"
    )
    cinematic_path = VISUALS_DIR / f"visual_{safe_id}_{index:03d}.jpg"
    try:
        pollinations_cinematic_image(prompt, cinematic_path, seed=numeric_seed * 1000 + index)
        return cinematic_path, "ai_cinematic", "dark cinematic still"
    except Exception as exc:
        print(f"[visual] AI image failed for shot {index} ({exc}); using atmospheric placeholder.")
        make_placeholder_visual(shot, cinematic_path, seed=numeric_seed * 1000 + index)
        return cinematic_path, "placeholder", "atmospheric placeholder"


# ─── MAIN VIDEO BUILDER ───────────────────────────────────────────────────────
def extract_thumbnail_source_frame(video_path: Path) -> Path:
    """Fallback for when every shot used stock video (no still image to reuse)."""
    frame_path = video_path.with_name(video_path.stem + "_thumb_source.jpg")
    try:
        with VideoFileClip(str(video_path)) as clip:
            t = min(2.0, max(0.0, clip.duration * 0.1))
            clip.save_frame(str(frame_path), t=t)
    except Exception as exc:
        print(f"Thumbnail source frame extraction failed (non-fatal): {exc}")
    return frame_path


def build_chapters(shots, shot_durations, video_type):
    """
    Builds YouTube chapter markers ("0:00 Title") from per-shot durations so
    long-form videos get "key moments". YouTube requires the first chapter to
    start at 0:00 and at least three chapters, each >= 10 seconds. Shorts are
    never chaptered. Returns a description-ready string, or "" if not eligible.
    """
    if not ENABLE_CHAPTERS:
        return ""
    normalized = str(video_type or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized == "short":
        return ""
    # Group shots into scenes via the scene_index carried on each shot.
    scene_starts = []  # (start_seconds, scene_title)
    elapsed = 0.0
    last_scene = None
    for shot, dur in zip(shots, shot_durations):
        scene_idx = shot.get("scene_index")
        if scene_idx != last_scene:
            title = (shot.get("scene_title") or shot.get("chapter_title") or "").strip()
            scene_starts.append((elapsed, title, scene_idx))
            last_scene = scene_idx
        elapsed += dur

    if len(scene_starts) < CHAPTERS_MIN_SCENES:
        return ""

    def fmt(t):
        t = int(round(t))
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    lines = []
    for n, (start, title, _idx) in enumerate(scene_starts, start=1):
        # YouTube needs the very first marker at 0:00 exactly.
        stamp = "0:00" if n == 1 else fmt(start)
        label = title if title else f"Part {n}"
        lines.append(f"{stamp} {label}")

    # Enforce minimum 10s gaps by dropping markers too close to the previous one.
    cleaned = [lines[0]]
    prev_t = 0.0
    for (start, title, _idx), line in zip(scene_starts[1:], lines[1:]):
        if start - prev_t >= 10:
            cleaned.append(line)
            prev_t = start
    if len(cleaned) < 3:
        return ""
    return "Chapters:\n" + "\n".join(cleaned)


def create_video(video_id, title, scene_payload, video_type="horror_story"):
    shots = flatten_story(scene_payload)
    if len(shots) < 8:
        raise ValueError(f"Too few shots ({len(shots)}). Regenerate story first.")
    safe_id    = re.sub(r"[^A-Za-z0-9_-]", "_", str(video_id).strip() or "video")
    video_path = VIDEO_DIR / f"nightfall_diaries_{safe_id}.mp4"
    seg_paths, voice_sources, visual_sources = [], [], []
    temp_paths_to_clean = []
    thumb_source_path = None
    shot_durations = []
    n_shots = len(shots)
    numeric_seed = abs(hash(safe_id)) % (10 ** 8)

    # ── Parallel pre-fetch: audio + visuals ──────────────────────────────────
    # Both are network-bound (edge-tts → Microsoft, images → Pollinations.ai).
    # Running them in parallel cuts total I/O wait from N×(audio+image) to
    # roughly max(single_audio, single_image). Rendering stays sequential so
    # peak RAM stays flat (the OOM fix).
    print(f"[prefetch] Starting parallel fetch of {n_shots} audio clips and visuals…")
    audio_cache  = {}   # {1-based index: (path, voice_source)}
    visual_cache = {}   # {1-based index: (path, visual_source, query)}

    def _fetch_audio_task(args):
        idx, shot = args
        return idx, create_shot_audio(shot, safe_id, idx)

    def _fetch_visual_task(args):
        idx, shot = args
        return idx, fetch_visual(shot, safe_id, idx, numeric_seed)

    # Audio and visuals can run truly concurrently in one pool.
    # Cap audio at 4 workers (edge-tts rate limits) and visuals at 6 (Pollinations).
    with ThreadPoolExecutor(max_workers=10) as pool:
        audio_futs  = {pool.submit(_fetch_audio_task,  (i, s)): i for i, s in enumerate(shots, 1)}
        visual_futs = {pool.submit(_fetch_visual_task, (i, s)): i for i, s in enumerate(shots, 1)}
        all_futs = {**audio_futs, **visual_futs}
        done_a = done_v = 0
        for fut in futures_as_completed(all_futs):
            if fut in audio_futs:
                idx, result = fut.result()
                audio_cache[idx] = result
                done_a += 1
                print(f"[prefetch] audio {done_a}/{n_shots}")
            else:
                idx, result = fut.result()
                visual_cache[idx] = result
                done_v += 1
                print(f"[prefetch] visual {done_v}/{n_shots}")
    print(f"[prefetch] Done. Starting render loop…")

    for i, shot in enumerate(shots, start=1):
        audio_path, voice_source = audio_cache[i]
        voice_sources.append(voice_source)
        audio_clip = AudioFileClip(str(audio_path))
        duration = max(3.0, audio_clip.duration + min(0.6, max(0.15, float(shot.get("pause_after", 0.25) or 0.25))))
        shot_durations.append(duration)

        visual_path, visual_source, query = visual_cache[i]
        visual_sources.append(visual_source)

        if visual_path.suffix.lower() == ".mp4":
            base_clip = stock_video_clip(visual_path, duration)
            caption_path = make_subtitle_overlay(safe_id, i, shot, title)
            caption_clip = ImageClip(str(caption_path)).set_duration(duration)
            clip = CompositeVideoClip([base_clip, caption_clip], size=(WIDTH, HEIGHT)).set_audio(audio_clip)
        else:
            if thumb_source_path is None:
                thumb_source_path = visual_path
            frame_path = make_frame(safe_id, i, shot, title, visual_path, len(shots))
            clip = animated_photo_clip(frame_path, duration, shot.get("camera_motion", "slow_zoom_in")).set_audio(audio_clip)

        # Apply fade transitions per-segment (fade-to-black between shots).
        # First shot: no fadein; last shot: no fadeout. Matches original behaviour.
        if ENABLE_TRANSITIONS and TRANSITION_SECONDS > 0:
            fade = min(TRANSITION_SECONDS, max(0.0, clip.duration / 3.0))
            if fade > 0:
                if i > 1:
                    clip = fadein(clip, fade)
                if i < n_shots:
                    clip = fadeout(clip, fade)

        # Render this shot to a temp segment and immediately free memory.
        # Processing one clip at a time keeps peak RAM ~constant regardless of
        # story length, avoiding the OOM kill that occurred when all clips were
        # accumulated before calling concatenate_videoclips.
        seg_path = VIDEO_DIR / f"seg_{safe_id}_{i:04d}.mp4"
        clip.write_videofile(
            str(seg_path),
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            preset="faster",
            threads=2,
            bitrate="4000k",
            ffmpeg_params=["-crf", "20", "-pix_fmt", "yuv420p"],
        )
        try:
            if clip.audio:
                clip.audio.close()
            clip.close()
        except Exception:
            pass
        seg_paths.append(seg_path)
        temp_paths_to_clean.append(seg_path)
        print(f"[{i}/{n_shots}] Shot rendered.")
        time.sleep(0.1)

    # End screen: for long-form videos, append a short outro card nudging the
    # viewer to watch another story. The linked end-screen element itself is set
    # in YouTube Studio; this on-screen card earns the extra session time. No
    # extra TTS call (keeps it free-tier safe); the ambient bed carries it.
    normalized_type_for_outro = str(video_type or "").strip().lower().replace("-", "_").replace(" ", "_")
    if ENABLE_END_SCREEN and END_SCREEN_SECONDS > 0 and normalized_type_for_outro != "short" and seg_paths:
        try:
            outro_path = make_end_screen_frame(safe_id, title)
            outro_clip = animated_photo_clip(outro_path, float(END_SCREEN_SECONDS), "slow_zoom_in")
            if ENABLE_TRANSITIONS and TRANSITION_SECONDS > 0:
                fade = min(TRANSITION_SECONDS, max(0.0, outro_clip.duration / 3.0))
                if fade > 0:
                    outro_clip = fadein(outro_clip, fade)
            seg_end = VIDEO_DIR / f"seg_{safe_id}_end.mp4"
            outro_clip.write_videofile(
                str(seg_end),
                fps=FPS, codec="libx264", audio_codec="aac",
                preset="faster", threads=2, bitrate="4000k",
                ffmpeg_params=["-crf", "20", "-pix_fmt", "yuv420p"],
            )
            outro_clip.close()
            seg_paths.append(seg_end)
            temp_paths_to_clean.append(seg_end)
            shot_durations.append(float(END_SCREEN_SECONDS))
        except Exception as exc:
            print(f"End screen skipped (non-fatal): {exc}")

    # Concatenate all segments using ffmpeg concat demuxer (stream copy — no
    # re-encode, so the final file is assembled in seconds regardless of length).
    concat_list = VIDEO_DIR / f"concat_{safe_id}.txt"
    temp_paths_to_clean.append(concat_list)
    with open(str(concat_list), "w") as f:
        for sp in seg_paths:
            f.write(f"file '{sp.resolve()}'\n")
    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c", "copy",
            "-movflags", "+faststart",
            str(video_path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed:\n{result.stderr[-3000:]}")
    print(f"Concatenated {len(seg_paths)} segments → {video_path}")

    # Clean up temp segment files
    for tp in temp_paths_to_clean:
        try:
            Path(tp).unlink(missing_ok=True)
        except Exception:
            pass

    normalized_type = str(video_type or "horror_story").strip().lower().replace("-", "_").replace(" ", "_")
    ambient_volume = AMBIENT_BED_VOLUME.get(normalized_type, DEFAULT_AMBIENT_BED_VOLUME)
    sting_volume = BRAND_STING_VOLUME if ENABLE_BRAND_STING else 0.0

    ambient_applied = False
    if ENABLE_AMBIENT_BED or sting_volume > 0:
        ambient_applied = add_ambient_bed(video_path, ambient_volume, sting_volume)

    loudness_applied = normalize_final_loudness(video_path, LOUDNESS_TARGET_LUFS)

    if thumb_source_path is None:
        thumb_source_path = extract_thumbnail_source_frame(video_path)
    thumb_path = generate_thumbnail(safe_id, title, thumb_source_path)

    chapters_text = build_chapters(shots, shot_durations, video_type)

    summary = (
        ",".join(sorted(set(voice_sources)))
        + f" | visuals={','.join(sorted(set(visual_sources)))}"
        + f" | shots={len(shots)}"
        + f" | ambient={'on' if ambient_applied else 'off'}"
        + f" | loudnorm={'on' if loudness_applied else 'off'}"
        + f" | chapters={'on' if chapters_text else 'off'}"
    )
    return video_path, summary, thumb_path, chapters_text


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────
def main():
    client = get_sheets_client()
    spreadsheet = open_spreadsheet(client)
    content_sheet = get_worksheet(spreadsheet, CONTENT_SHEET_NAME)
    logs_sheet    = get_logs_worksheet(spreadsheet)
    values  = get_all_values(content_sheet)
    headers = values[0]

    id_col           = find_column(headers, "id")
    title_col        = find_column(headers, "title")
    status_col       = find_column(headers, "status")
    scene_prompts_col = find_column(headers, "scene_prompts")
    image_status_col = find_column(headers, "image_status")
    audio_status_col = find_column(headers, "audio_status")
    video_type_col   = find_optional_column(headers, "video_type")
    error_message_col = find_optional_column(headers, "error_message")
    thumbnail_path_col = find_optional_column(headers, "thumbnail_path")

    requested_video_type = (
        (os.getenv("TBT_VIDEO_TYPE", "") or "")
        .strip().lower().replace("-", "_").replace(" ", "_")
    )

    target_row_number, target_row = None, None
    for index, row in enumerate(values[1:], start=2):
        if get_cell(row, status_col).upper() == "GENERATED":
            row_type = get_cell(row, video_type_col).lower() if video_type_col else ""
            if requested_video_type and row_type and row_type != requested_video_type:
                continue
            target_row_number, target_row = index, row
            break

    if target_row_number is None:
        log(logs_sheet, "", "GENERATE_VIDEO", "No GENERATED row found.")
        print("No GENERATED row found.")
        return

    video_id   = get_cell(target_row, id_col)
    title      = get_cell(target_row, title_col)
    scene_raw  = get_cell(target_row, scene_prompts_col)
    row_video_type = get_cell(target_row, video_type_col) if video_type_col else "horror_story"
    if not title or not scene_raw:
        raise ValueError("Missing title or scene_prompts.")
    scene_payload = json.loads(scene_raw)

    try:
        video_path, voice_source, thumb_path, chapters_text = create_video(video_id, title, scene_payload, row_video_type)
    except Exception as exc:
        if error_message_col:
            update_cell(content_sheet, target_row_number, error_message_col, str(exc)[:1500])
        log(logs_sheet, video_id, "GENERATE_VIDEO_ERROR", str(exc))
        raise

    update_cell(content_sheet, target_row_number, status_col,       "VIDEO_CREATED")
    update_cell(content_sheet, target_row_number, image_status_col, "CREATED")
    update_cell(content_sheet, target_row_number, audio_status_col, voice_source)
    update_optional(content_sheet, target_row_number, thumbnail_path_col, str(thumb_path))

    # Prepend chapter markers to the description so the uploader picks them up
    # and YouTube shows "key moments" on long-form videos. Only if we built any
    # and they aren't already present.
    if chapters_text:
        description_col = find_optional_column(headers, "description")
        if description_col:
            existing_desc = get_cell(target_row, description_col)
            if "Chapters:" not in existing_desc:
                merged = (chapters_text + "\n\n" + existing_desc).strip()
                update_optional(content_sheet, target_row_number, description_col, merged[:49000])

    if error_message_col:
        update_cell(content_sheet, target_row_number, error_message_col, "")
    log(logs_sheet, video_id, "GENERATE_VIDEO",
        f"Created video: {video_path}. Voice: {voice_source}. Thumbnail: {thumb_path}. Chapters: {'yes' if chapters_text else 'no'}")
    print(f"Video created: {video_path}")
    print(f"Voice source: {voice_source}")
    print(f"Thumbnail created: {thumb_path}")
    if chapters_text:
        print("Chapters added to description.")


if __name__ == "__main__":
    main()
