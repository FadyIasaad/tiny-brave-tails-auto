import asyncio
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import edge_tts
import gspread
import numpy as np
import requests
from google.oauth2.service_account import Credentials
from gtts import gTTS
from moviepy.audio.AudioClip import AudioArrayClip
from moviepy.editor import (
    AudioFileClip,
    CompositeVideoClip,
    ImageClip,
    concatenate_audioclips,
    concatenate_videoclips,
)
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageStat

# Compatibility fix:
# MoviePy 1.0.3 still calls PIL.Image.ANTIALIAS internally.
# Pillow 10+ removed Image.ANTIALIAS, which causes GitHub Actions to crash
# during animated resize/zoom. Keep this alias so the video engine works even
# if a newer Pillow version is installed by the runner cache or dependencies.
if not hasattr(Image, "ANTIALIAS"):
    try:
        Image.ANTIALIAS = Image.Resampling.LANCZOS
    except AttributeError:
        Image.ANTIALIAS = Image.LANCZOS

SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

CONTENT_SHEET_NAME = "Content"
LOGS_SHEET_NAME = "Logs"

OUTPUT_DIR = Path("output")
VISUALS_DIR = OUTPUT_DIR / "visuals"
OVERLAYS_DIR = OUTPUT_DIR / "overlays"
AUDIO_DIR = OUTPUT_DIR / "audio"

for directory in [OUTPUT_DIR, VISUALS_DIR, OVERLAYS_DIR, AUDIO_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

WIDTH = 1080
HEIGHT = 1920
FPS = 24

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

VOICE_PROFILES = {
    "narrator": {
        "voice": "en-US-GuyNeural",
        "rate": "-12%",
        "pitch": "-8Hz",
        "volume": "+0%",
    },
    "small_hero": {
        "voice": "en-US-AnaNeural",
        "rate": "-6%",
        "pitch": "+18Hz",
        "volume": "+4%",
    },
    "female_warm": {
        "voice": "en-US-JennyNeural",
        "rate": "-8%",
        "pitch": "+2Hz",
        "volume": "+2%",
    },
    "wise_elder": {
        "voice": "en-US-GuyNeural",
        "rate": "-22%",
        "pitch": "-18Hz",
        "volume": "+0%",
    },
    "danger": {
        "voice": "en-US-ChristopherNeural",
        "rate": "-25%",
        "pitch": "-24Hz",
        "volume": "+5%",
    },
    "ending": {
        "voice": "en-US-AriaNeural",
        "rate": "-7%",
        "pitch": "+4Hz",
        "volume": "+4%",
    },
}

FALLBACK_PROFILE = VOICE_PROFILES["narrator"]

MOTION_FALLBACKS = [
    "slow_zoom_in",
    "slow_zoom_out",
    "pan_left",
    "pan_right",
    "rise_up",
    "drift_down",
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


def stable_seed(value):
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def clean_text(value, max_len=None):
    value = re.sub(r"\s+", " ", str(value or "").replace("\n", " ")).strip()
    if max_len and len(value) > max_len:
        value = value[: max_len - 1].rstrip() + "…"
    return value


def load_font(size, bold=True):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def wrap_text(draw, text, font, max_width, max_lines=4):
    words = clean_text(text).split()
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

    if len(lines) == max_lines and len(" ".join(words)) > len(" ".join(lines)):
        lines[-1] = lines[-1].rstrip(".,;: ") + "…"

    return lines[:max_lines]


def draw_centered_lines(draw, lines, font, center_y, fill, spacing=10, shadow=True):
    if not lines:
        return

    heights = []
    widths = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        widths.append(bbox[2] - bbox[0])
        heights.append(bbox[3] - bbox[1])

    total_h = sum(heights) + spacing * max(0, len(lines) - 1)
    y = center_y - total_h // 2

    for line, w, h in zip(lines, widths, heights):
        x = (WIDTH - w) // 2
        if shadow:
            draw.text((x + 4, y + 4), line, font=font, fill=(0, 0, 0, 210))
        draw.text((x, y), line, font=font, fill=fill)
        y += h + spacing


def validate_image(path):
    with Image.open(path) as img:
        img = img.convert("RGB")
        if img.size[0] < 500 or img.size[1] < 500:
            raise ValueError(f"Image too small: {img.size}")
        thumb = img.resize((64, 64))
        stat = ImageStat.Stat(thumb)
        brightness = sum(stat.mean) / 3
        if brightness < 10:
            raise ValueError("Image is almost black")
    return True


def pollinations_image(prompt, output_path, seed):
    visual_prompt = f"""
warm cinematic 2D cartoon storybook illustration, emotional animal story, cute expressive animal character,
soft colors, beautiful lighting, clear main subject, vertical 9:16 composition, detailed background,
full visible scene, no text, no logo, no watermark, not blurry, not black.
Scene: {prompt}
""".strip()

    encoded = quote_plus(visual_prompt)
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={WIDTH}&height={HEIGHT}&seed={seed}&nologo=true&enhance=true&model=flux"
    )

    response = requests.get(url, timeout=180)
    response.raise_for_status()

    content_type = response.headers.get("content-type", "").lower()
    if "image" not in content_type and len(response.content) < 10_000:
        raise ValueError(f"Pollinations returned non-image response: {content_type}")

    output_path.write_bytes(response.content)
    validate_image(output_path)
    return output_path


def make_fallback_visual(output_path, title, scene_number):
    img = Image.new("RGB", (WIDTH, HEIGHT), "#162033")
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        r = int(22 + ratio * 34)
        g = int(32 + ratio * 28)
        b = int(51 + ratio * 48)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))

    rng = random.Random(scene_number * 991)
    for _ in range(130):
        x = rng.randint(0, WIDTH)
        y = rng.randint(0, HEIGHT)
        radius = rng.randint(1, 4)
        shade = rng.randint(110, 210)
        draw.ellipse((x, y, x + radius, y + radius), fill=(shade, shade, shade))

    font_big = load_font(58, True)
    font_small = load_font(34, False)
    draw.text((70, 780), "Tiny Brave Tails", font=font_big, fill=(255, 235, 185))
    draw.text((70, 860), clean_text(title, 42), font=font_small, fill=(245, 245, 245))
    draw.text((70, 930), f"Scene {scene_number}", font=font_small, fill=(255, 235, 185))
    img.save(output_path, quality=95)
    return output_path


def create_visual(prompt, output_path, seed_base, title, scene_number, previous_good_path=None):
    errors = []
    for attempt in range(1, 4):
        seed = seed_base + scene_number * 97 + attempt * 13
        try:
            print(f"Generating visual scene {scene_number}, attempt {attempt}, seed {seed}")
            pollinations_image(prompt, output_path, seed)
            print(f"Visual created: {output_path}")
            return output_path, "generated"
        except Exception as exc:
            errors.append(str(exc))
            print(f"Visual failed scene {scene_number}, attempt {attempt}: {exc}")
            time.sleep(1.5 * attempt)

    if previous_good_path and Path(previous_good_path).exists():
        shutil.copyfile(previous_good_path, output_path)
        print(f"Reused previous visual for scene {scene_number}: {previous_good_path}")
        return output_path, "reused_previous"

    make_fallback_visual(output_path, title, scene_number)
    print(f"Used fallback visual for scene {scene_number}. Errors: {' | '.join(errors)}")
    return output_path, "fallback"


def prepare_background(path):
    try:
        img = Image.open(path).convert("RGB")
        img = ImageOps.fit(img, (WIDTH, HEIGHT), method=Image.LANCZOS, centering=(0.5, 0.5))
    except Exception:
        img = Image.new("RGB", (WIDTH, HEIGHT), "#162033")

    img = ImageEnhance.Contrast(img).enhance(1.06)
    img = ImageEnhance.Color(img).enhance(1.08)
    return img


def create_background_frame(image_path, output_path):
    bg = prepare_background(image_path).convert("RGBA")
    vignette = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    vdraw = ImageDraw.Draw(vignette)

    for i in range(70):
        alpha = int(3.0 * i)
        vdraw.rectangle((i, i, WIDTH - i, HEIGHT - i), outline=(0, 0, 0, max(0, 180 - alpha)))

    cinematic_overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 28))
    final = Image.alpha_composite(bg, cinematic_overlay)
    final.save(output_path)
    return output_path


def draw_particles(draw, atmosphere, scene_number):
    rng = random.Random(scene_number * 2027)
    atmosphere = (atmosphere or "").lower()

    if "rain" in atmosphere or "storm" in atmosphere:
        for _ in range(75):
            x = rng.randint(0, WIDTH)
            y = rng.randint(0, HEIGHT)
            length = rng.randint(20, 46)
            draw.line((x, y, x + 8, y + length), fill=(210, 225, 255, 55), width=2)
    elif "fog" in atmosphere or "mist" in atmosphere:
        for _ in range(18):
            x = rng.randint(-150, WIDTH)
            y = rng.randint(150, HEIGHT - 250)
            w = rng.randint(180, 420)
            h = rng.randint(40, 95)
            draw.ellipse((x, y, x + w, y + h), fill=(235, 235, 235, 22))
    else:
        for _ in range(70):
            x = rng.randint(0, WIDTH)
            y = rng.randint(0, HEIGHT)
            r = rng.randint(1, 4)
            draw.ellipse((x, y, x + r, y + r), fill=(255, 236, 180, rng.randint(25, 80)))


def create_overlay(scene, title, scene_index, total_scenes, output_path):
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    top = Image.new("RGBA", (WIDTH, 250), (0, 0, 0, 125))
    overlay.alpha_composite(top, (0, 0))

    bottom_h = 520
    bottom_y = HEIGHT - bottom_h - 55
    bottom = Image.new("RGBA", (WIDTH - 90, bottom_h), (0, 0, 0, 172))
    bottom = bottom.filter(ImageFilter.GaussianBlur(1))
    overlay.alpha_composite(bottom, (45, bottom_y))

    draw_particles(draw, scene.get("atmosphere"), scene_index)

    brand_font = load_font(44, True)
    title_font = load_font(30, False)
    subtitle_font = load_font(56, True)
    small_font = load_font(28, False)

    draw.text((55, 38), "Tiny Brave Tails", font=brand_font, fill=(255, 237, 185, 255))

    title_lines = wrap_text(draw, title, title_font, 950, max_lines=2)
    y = 106
    for line in title_lines:
        draw.text((55, y), line, font=title_font, fill=(245, 245, 245, 230))
        y += 38

    subtitle = clean_text(scene.get("subtitle_en") or scene.get("narration_en"), 260)
    subtitle_lines = wrap_text(draw, subtitle, subtitle_font, 920, max_lines=4)
    draw_centered_lines(
        draw,
        subtitle_lines,
        subtitle_font,
        bottom_y + 205,
        fill=(255, 255, 255, 255),
        spacing=12,
    )

    # Scene progress bar
    bar_x = 120
    bar_y = HEIGHT - 98
    bar_w = 840
    bar_h = 12
    progress = scene_index / max(1, total_scenes)
    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + bar_w, bar_y + bar_h),
        radius=8,
        fill=(255, 255, 255, 70),
    )
    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + int(bar_w * progress), bar_y + bar_h),
        radius=8,
        fill=(255, 232, 170, 245),
    )

    cta = "Follow for tiny stories with big lessons"
    bbox = draw.textbbox((0, 0), cta, font=small_font)
    draw.text(
        ((WIDTH - (bbox[2] - bbox[0])) // 2, HEIGHT - 62),
        cta,
        font=small_font,
        fill=(255, 255, 255, 220),
    )

    overlay.save(output_path)
    return output_path


async def edge_tts_save(text, output_path, profile):
    communicate = edge_tts.Communicate(
        text=text,
        voice=profile["voice"],
        rate=profile["rate"],
        volume=profile["volume"],
        pitch=profile["pitch"],
    )
    await communicate.save(str(output_path))


def create_edge_audio(text, output_path, profile):
    asyncio.run(edge_tts_save(text, output_path, profile))
    if not output_path.exists() or output_path.stat().st_size < 1000:
        raise RuntimeError("Edge TTS returned an empty audio file")
    return output_path


def create_gtts_audio(text, output_path):
    tts = gTTS(text=text, lang="en", slow=False, tld="com")
    tts.save(str(output_path))
    return output_path


def create_espeak_audio(text, output_path):
    command = [
        "espeak-ng",
        "-v",
        "en-us",
        "-s",
        "132",
        "-p",
        "38",
        "-a",
        "175",
        "-w",
        str(output_path),
        text,
    ]
    subprocess.run(command, check=True)
    return output_path


def normalize_voice_role(role):
    role = clean_text(role).lower().replace(" ", "_").replace("-", "_")
    return role if role in VOICE_PROFILES else "narrator"


def prepare_spoken_text(text, voice_role):
    text = clean_text(text, 240)
    if voice_role in {"danger", "wise_elder"} and "..." not in text and "—" not in text:
        text = text.replace(", ", "... ", 1) if ", " in text else text + "..."
    return text


def make_silence_clip(duration=0.18, fps=44100):
    frames = max(1, int(duration * fps))
    audio = np.zeros((frames, 2), dtype=np.float32)
    return AudioArrayClip(audio, fps=fps)


def scene_lines(scene):
    lines = scene.get("lines")
    if isinstance(lines, list) and lines:
        cleaned = []
        for line in lines:
            if not isinstance(line, dict):
                continue
            text = clean_text(line.get("text"), 240)
            if text:
                cleaned.append(
                    {
                        "speaker": clean_text(line.get("speaker"), 60) or "Narrator",
                        "voice_role": normalize_voice_role(line.get("voice_role")),
                        "text": text,
                    }
                )
        if cleaned:
            return cleaned

    narration = clean_text(scene.get("narration_en") or scene.get("subtitle_en"), 360)
    if narration:
        return [{"speaker": "Narrator", "voice_role": "narrator", "text": narration}]

    raise ValueError("Scene has no speakable English text")


def create_line_audio(text, voice_role, safe_id, scene_index, line_index):
    role = normalize_voice_role(voice_role)
    profile = VOICE_PROFILES.get(role, FALLBACK_PROFILE)
    spoken_text = prepare_spoken_text(text, role)

    edge_path = AUDIO_DIR / f"line_{safe_id}_{scene_index:02d}_{line_index:02d}_edge.mp3"
    gtts_path = AUDIO_DIR / f"line_{safe_id}_{scene_index:02d}_{line_index:02d}_gtts.mp3"
    wav_path = AUDIO_DIR / f"line_{safe_id}_{scene_index:02d}_{line_index:02d}_espeak.wav"

    try:
        create_edge_audio(spoken_text, edge_path, profile)
        return edge_path, f"edge:{profile['voice']}:{role}"
    except Exception as exc:
        print(f"Edge TTS failed for scene {scene_index} line {line_index} role {role}: {exc}")

    if profile != FALLBACK_PROFILE:
        try:
            create_edge_audio(spoken_text, edge_path, FALLBACK_PROFILE)
            return edge_path, f"edge:{FALLBACK_PROFILE['voice']}:fallback"
        except Exception as exc:
            print(f"Edge fallback failed: {exc}")

    try:
        create_gtts_audio(spoken_text, gtts_path)
        return gtts_path, "gTTS:fallback"
    except Exception as exc:
        print(f"gTTS failed: {exc}")

    create_espeak_audio(spoken_text, wav_path)
    return wav_path, "espeak-ng:fallback"


def create_scene_audio(scene, safe_id, scene_index):
    line_audio_paths = []
    voice_sources = []

    for line_index, line in enumerate(scene_lines(scene), start=1):
        audio_path, source = create_line_audio(
            text=line["text"],
            voice_role=line["voice_role"],
            safe_id=safe_id,
            scene_index=scene_index,
            line_index=line_index,
        )
        line_audio_paths.append(audio_path)
        voice_sources.append(source)

    clips = []
    try:
        for idx, audio_path in enumerate(line_audio_paths):
            clips.append(AudioFileClip(str(audio_path)))
            if idx < len(line_audio_paths) - 1:
                clips.append(make_silence_clip(0.20))

        if len(clips) == 1:
            combined = clips[0]
        else:
            combined = concatenate_audioclips(clips)

        final_path = AUDIO_DIR / f"scene_{safe_id}_{scene_index:02d}.mp3"
        combined.write_audiofile(str(final_path), fps=44100, codec="libmp3lame", logger=None)
        return final_path, voice_sources
    finally:
        for clip in clips:
            try:
                clip.close()
            except Exception:
                pass


def animated_background_clip(bg_path, duration, motion):
    motion = clean_text(motion).lower() or random.choice(MOTION_FALLBACKS)
    base = ImageClip(str(bg_path)).set_duration(duration)

    if motion not in MOTION_FALLBACKS:
        motion = random.choice(MOTION_FALLBACKS)

    zoom = 0.075

    if motion == "slow_zoom_out":
        animated = base.resize(lambda t: 1.0 + zoom * (1 - min(t / duration, 1)))
        return animated.set_position("center")

    if motion == "pan_left":
        scaled = base.resize(1.08)
        start_x, end_x = 0, WIDTH - int(WIDTH * 1.08)
        return scaled.set_position(lambda t: (start_x + (end_x - start_x) * min(t / duration, 1), "center"))

    if motion == "pan_right":
        scaled = base.resize(1.08)
        start_x, end_x = WIDTH - int(WIDTH * 1.08), 0
        return scaled.set_position(lambda t: (start_x + (end_x - start_x) * min(t / duration, 1), "center"))

    if motion == "rise_up":
        scaled = base.resize(1.08)
        start_y, end_y = 0, HEIGHT - int(HEIGHT * 1.08)
        return scaled.set_position(lambda t: ("center", start_y + (end_y - start_y) * min(t / duration, 1)))

    if motion == "drift_down":
        scaled = base.resize(1.08)
        start_y, end_y = HEIGHT - int(HEIGHT * 1.08), 0
        return scaled.set_position(lambda t: ("center", start_y + (end_y - start_y) * min(t / duration, 1)))

    # default slow zoom in
    animated = base.resize(lambda t: 1.0 + zoom * min(t / duration, 1))
    return animated.set_position("center")


def create_scene_clip(scene, title, safe_id, scene_index, total_scenes, image_path):
    audio_path, voice_sources = create_scene_audio(scene, safe_id, scene_index)
    audio_clip = AudioFileClip(str(audio_path))
    duration = max(3.2, min(8.0, audio_clip.duration + 0.35))

    bg_frame = OVERLAYS_DIR / f"bg_{safe_id}_{scene_index:02d}.png"
    overlay_frame = OVERLAYS_DIR / f"overlay_{safe_id}_{scene_index:02d}.png"

    create_background_frame(image_path, bg_frame)
    create_overlay(scene, title, scene_index, total_scenes, overlay_frame)

    bg_clip = animated_background_clip(bg_frame, duration, scene.get("camera_motion"))
    overlay_clip = ImageClip(str(overlay_frame)).set_duration(duration)

    clip = CompositeVideoClip([bg_clip, overlay_clip], size=(WIDTH, HEIGHT)).set_duration(duration).set_audio(audio_clip)
    return clip, voice_sources


def normalize_scene_payload(scene_payload):
    if not isinstance(scene_payload, dict):
        raise ValueError("scene_prompts JSON must be an object")
    scenes = scene_payload.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("scene_prompts must contain a non-empty scenes list")
    return scene_payload


def create_video(video_id, title, scene_payload):
    scene_payload = normalize_scene_payload(scene_payload)
    scenes = scene_payload["scenes"]
    character = scene_payload.get("character", {}) or {}
    char_desc = clean_text(character.get("description"), 650)

    safe_id = safe_filename(video_id)
    video_path = OUTPUT_DIR / f"tiny_brave_tails_{safe_id}.mp4"

    clips = []
    all_voice_sources = []
    visual_sources = []
    previous_good_visual = None
    seed_base = stable_seed(safe_id)
    total_scenes = len(scenes)

    for i, scene in enumerate(scenes, start=1):
        prompt = clean_text(scene.get("image_prompt"), 1200)
        if char_desc:
            prompt = f"Main character design: {char_desc}. Scene: {prompt}"
        if not prompt:
            prompt = f"Scene {i} from an emotional animal story called {title}."

        visual_path = VISUALS_DIR / f"visual_{safe_id}_{i:02d}.jpg"
        visual_path, visual_source = create_visual(
            prompt=prompt,
            output_path=visual_path,
            seed_base=seed_base,
            title=title,
            scene_number=i,
            previous_good_path=previous_good_visual,
        )
        visual_sources.append(f"{i}:{visual_source}")
        if visual_source == "generated":
            previous_good_visual = visual_path

        clip, voice_sources = create_scene_clip(
            scene=scene,
            title=title,
            safe_id=safe_id,
            scene_index=i,
            total_scenes=total_scenes,
            image_path=visual_path,
        )
        clips.append(clip)
        all_voice_sources.extend(voice_sources)

    video = concatenate_videoclips(clips, method="compose")
    try:
        video.write_videofile(
            str(video_path),
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            preset="medium",
            threads=2,
            bitrate="5200k",
            logger="bar",
        )
    finally:
        try:
            video.close()
        except Exception:
            pass
        for clip in clips:
            try:
                clip.close()
            except Exception:
                pass

    return video_path, sorted(set(all_voice_sources)), visual_sources


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

    try:
        if not title or not scene_raw:
            raise ValueError("Missing title or scene_prompts.")

        scene_payload = json.loads(scene_raw)
        video_path, voice_sources, visual_sources = create_video(video_id, title, scene_payload)

        content_sheet.update_cell(target_row_number, status_col, "VIDEO_CREATED")
        content_sheet.update_cell(target_row_number, image_status_col, "CREATED")
        content_sheet.update_cell(target_row_number, audio_status_col, ", ".join(voice_sources)[:450])
        update_optional(content_sheet, target_row_number, video_file_path_col, str(video_path))
        update_optional(content_sheet, target_row_number, error_message_col, "")

        log(
            logs_sheet,
            video_id,
            "GENERATE_VIDEO",
            f"Created Cinematic Free V2 video: {video_path}. Voices: {', '.join(voice_sources)[:500]}. Visuals: {', '.join(visual_sources)}",
        )
        print(f"Video created: {video_path}")
        print(f"Voices: {', '.join(voice_sources)}")
        print(f"Visuals: {', '.join(visual_sources)}")

    except Exception as exc:
        content_sheet.update_cell(target_row_number, status_col, "FAILED_VIDEO")
        update_optional(content_sheet, target_row_number, image_status_col, "FAILED")
        update_optional(content_sheet, target_row_number, audio_status_col, "FAILED")
        update_optional(content_sheet, target_row_number, error_message_col, str(exc)[:500])
        log(logs_sheet, video_id, "FAILED_VIDEO", str(exc)[:1000])
        raise


if __name__ == "__main__":
    main()
