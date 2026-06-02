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
from moviepy.editor import AudioFileClip, ImageClip, concatenate_videoclips
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# MoviePy 1.0.3 compatibility with newer Pillow builds.
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.LANCZOS

from tbt_common import (
    find_column,
    get_all_values,
    get_cell,
    get_sheets_client,
    get_worksheet,
    log,
    open_spreadsheet,
    update_cell,
)

CONTENT_SHEET_NAME = "Content"
LOGS_SHEET_NAME = "Logs"
OUTPUT_DIR = Path("output")
FRAMES_DIR = OUTPUT_DIR / "frames"
VISUALS_DIR = OUTPUT_DIR / "visuals"
AUDIO_DIR = OUTPUT_DIR / "audio"
VIDEO_DIR = OUTPUT_DIR / "videos"
for folder in [OUTPUT_DIR, FRAMES_DIR, VISUALS_DIR, AUDIO_DIR, VIDEO_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

WIDTH = 1080
HEIGHT = 1920
FPS = 24
DEFAULT_VOICE = os.getenv("EDGE_TTS_VOICE", "en-US-JennyNeural")
BEDTIME_VOICE = os.getenv("EDGE_TTS_BEDTIME_VOICE", "en-US-AriaNeural")
LONG_NARRATION_VOICE = os.getenv("EDGE_TTS_LONG_VOICE", "en-US-AriaNeural")

EMOTION_STYLE = {
    "wonder": {"voice": LONG_NARRATION_VOICE, "rate": "-12%", "pitch": "-1Hz", "volume": "+0%"},
    "lonely": {"voice": LONG_NARRATION_VOICE, "rate": "-18%", "pitch": "-5Hz", "volume": "-1%"},
    "worried": {"voice": LONG_NARRATION_VOICE, "rate": "-16%", "pitch": "-3Hz", "volume": "+0%"},
    "afraid": {"voice": LONG_NARRATION_VOICE, "rate": "-15%", "pitch": "-6Hz", "volume": "+0%"},
    "brave": {"voice": LONG_NARRATION_VOICE, "rate": "-11%", "pitch": "+0Hz", "volume": "+1%"},
    "relieved": {"voice": LONG_NARRATION_VOICE, "rate": "-13%", "pitch": "-1Hz", "volume": "+0%"},
    "peaceful": {"voice": LONG_NARRATION_VOICE, "rate": "-18%", "pitch": "-4Hz", "volume": "-1%"},
}


def load_font(size, bold=True, arabic=False):
    paths = []
    if arabic:
        paths += [
            "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        ]
    paths += [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def reshape_arabic(text):
    text = (text or "").strip()
    if not text:
        return ""
    return get_display(arabic_reshaper.reshape(text))


def wrap_ltr(draw, text, font, max_width, max_lines=4):
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


def wrap_arabic(draw, text, font, max_width, max_lines=4):
    words = str(text or "").split()
    lines, current = [], ""
    for word in words:
        test = (current + " " + word).strip()
        visual = reshape_arabic(test)
        bbox = draw.textbbox((0, 0), visual, font=font)
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
    return [reshape_arabic(line) for line in lines[:max_lines]]


def draw_centered_lines(draw, lines, font, center_y, fill, spacing=9):
    heights = [draw.textbbox((0, 0), line, font=font)[3] - draw.textbbox((0, 0), line, font=font)[1] for line in lines]
    total_h = sum(heights) + spacing * max(0, len(lines) - 1)
    y = center_y - total_h // 2
    for line, h in zip(lines, heights):
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (WIDTH - (bbox[2] - bbox[0])) // 2
        draw.text((x + 4, y + 4), line, font=font, fill=(0, 0, 0, 210))
        draw.text((x, y), line, font=font, fill=fill)
        y += h + spacing


def fallback_background(output_path, emotion="peaceful"):
    palettes = {
        "afraid": ((18, 30, 55), (55, 70, 105)),
        "worried": ((35, 40, 60), (78, 80, 110)),
        "lonely": ((30, 42, 65), (75, 90, 120)),
        "brave": ((45, 35, 45), (135, 95, 75)),
        "relieved": ((45, 65, 75), (130, 120, 95)),
        "peaceful": ((35, 48, 68), (90, 95, 125)),
        "wonder": ((38, 45, 80), (120, 95, 145)),
    }
    top, bottom = palettes.get(emotion, palettes["peaceful"])
    img = Image.new("RGB", (WIDTH, HEIGHT), top)
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        color = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        draw.line([(0, y), (WIDTH, y)], fill=color)
    # moon + soft stars
    draw.ellipse((760, 130, 940, 310), fill=(250, 230, 170))
    for i in range(70):
        x = (i * 137) % WIDTH
        y = 70 + (i * 83) % 820
        r = 1 + (i % 3)
        draw.ellipse((x, y, x + r, y + r), fill=(255, 245, 205))
    img.save(output_path, quality=95)
    return output_path


def pollinations_image(prompt, output_path, seed):
    final_prompt = f"""
warm emotional 2D cartoon storybook illustration, consistent cute animal character,
soft cinematic bedtime lighting, expressive eyes, gentle painterly texture,
vertical 9:16 composition, child safe, no text, no watermark.
Scene: {prompt}
"""
    encoded = quote_plus(final_prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded}?width={WIDTH}&height={HEIGHT}&seed={seed}&nologo=true&enhance=true&model=flux"
    response = requests.get(url, timeout=150)
    response.raise_for_status()
    output_path.write_bytes(response.content)
    with Image.open(output_path) as img:
        img.verify()
    return output_path


def prepare_background(path):
    try:
        img = Image.open(path).convert("RGB").resize((WIDTH, HEIGHT), Image.LANCZOS)
    except Exception:
        fallback = FRAMES_DIR / "fallback_bg.jpg"
        img = Image.open(fallback_background(fallback)).convert("RGB")
    # subtle readability gradient
    rgba = img.convert("RGBA")
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 25))
    rgba = Image.alpha_composite(rgba, overlay)
    return rgba


def make_frame(video_id, scene_index, scene, title, image_path, total_scenes):
    bg = prepare_background(image_path)
    draw = ImageDraw.Draw(bg)
    brand_font = load_font(42, True)
    title_font = load_font(30, False)
    beat_font = load_font(28, False)
    en_font = load_font(40, True)
    ar_font = load_font(36, True, arabic=True)
    small_font = load_font(28, False)

    top = Image.new("RGBA", (WIDTH, 235), (0, 0, 0, 105))
    bg.alpha_composite(top, (0, 0))
    draw.text((55, 36), "Tiny Brave Tails", font=brand_font, fill=(255, 238, 190, 255))
    y = 98
    for line in wrap_ltr(draw, title, title_font, 940, 2):
        draw.text((55, y), line, font=title_font, fill=(245, 245, 245, 230))
        y += 38
    emotion = str(scene.get("emotion", "peaceful")).capitalize()
    draw.text((55, 178), f"{scene_index}/{total_scenes}  •  {emotion}", font=beat_font, fill=(255, 238, 190, 230))

    subtitle_h = 560
    subtitle_y = HEIGHT - subtitle_h - 75
    box = Image.new("RGBA", (WIDTH - 80, subtitle_h), (0, 0, 0, 155)).filter(ImageFilter.GaussianBlur(1))
    bg.alpha_composite(box, (40, subtitle_y))

    en_source = scene.get("subtitle_en") or scene.get("narration_en")
    ar_source = scene.get("subtitle_ar", "")
    en_lines = wrap_ltr(draw, en_source, en_font, 910, 5)
    ar_lines = wrap_arabic(draw, ar_source, ar_font, 910, 5)
    draw_centered_lines(draw, en_lines, en_font, subtitle_y + 180, (255, 255, 255, 255), 7)
    draw_centered_lines(draw, ar_lines, ar_font, subtitle_y + 405, (255, 232, 170, 255), 7)

    bar_x, bar_y, bar_w, bar_h = 120, HEIGHT - 92, 840, 12
    draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), radius=8, fill=(255, 255, 255, 65))
    draw.rounded_rectangle((bar_x, bar_y, bar_x + int(bar_w * scene_index / total_scenes), bar_y + bar_h), radius=8, fill=(255, 232, 170, 245))
    cta = "Soft animal stories with tiny courage"
    bbox = draw.textbbox((0, 0), cta, font=small_font)
    draw.text(((WIDTH - (bbox[2] - bbox[0])) // 2, HEIGHT - 58), cta, font=small_font, fill=(255, 255, 255, 220))

    frame_path = FRAMES_DIR / f"frame_{video_id}_{scene_index:02d}.jpg"
    bg.convert("RGB").save(frame_path, quality=95)
    return frame_path


async def create_edge_audio_async(text, output_path, emotion="peaceful"):
    style = EMOTION_STYLE.get(emotion, EMOTION_STYLE["peaceful"])
    communicate = edge_tts.Communicate(
        text=text,
        voice=style["voice"],
        rate=style["rate"],
        pitch=style["pitch"],
        volume=style["volume"],
    )
    await communicate.save(str(output_path))


def create_edge_audio(text, output_path, emotion="peaceful"):
    clean = re.sub(r"\s+", " ", str(text).replace("\n", " ")).strip()
    if not clean:
        raise ValueError("Empty narration text")
    asyncio.run(create_edge_audio_async(clean, output_path, emotion))
    return output_path


def create_espeak_audio(text, output_path):
    clean = re.sub(r"\s+", " ", str(text).replace("\n", " ")).strip()
    command = ["espeak-ng", "-v", "en-us", "-s", "132", "-p", "42", "-a", "155", "-w", str(output_path), clean]
    subprocess.run(command, check=True)
    return output_path


def normalize_audio(input_path, video_id, scene_index):
    """Normalize loudness so voices do not jump between scenes."""
    normalized = AUDIO_DIR / f"audio_{video_id}_{scene_index:02d}_norm.m4a"
    command = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-af", "loudnorm=I=-18:TP=-1.5:LRA=11",
        "-ar", "48000", "-ac", "2", "-c:a", "aac", "-b:a", "192k",
        str(normalized),
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return normalized
    except Exception as exc:
        print(f"Audio normalization failed, using original audio: {exc}")
        return input_path


def create_scene_audio(scene, video_id, scene_index):
    narration = scene.get("narration_en", "").strip()
    emotion = scene.get("emotion", "peaceful").strip().lower()
    mp3_path = AUDIO_DIR / f"audio_{video_id}_{scene_index:02d}.mp3"
    wav_path = AUDIO_DIR / f"audio_{video_id}_{scene_index:02d}.wav"
    try:
        create_edge_audio(narration, mp3_path, emotion)
        final_audio = normalize_audio(mp3_path, video_id, scene_index)
        return final_audio, f"edge-tts:{EMOTION_STYLE.get(emotion, EMOTION_STYLE['peaceful'])['voice']}:loudnorm"
    except Exception as exc:
        print(f"Edge TTS failed for scene {scene_index}: {exc}")
        create_espeak_audio(narration, wav_path)
        final_audio = normalize_audio(wav_path, video_id, scene_index)
        return final_audio, "espeak-ng:loudnorm"


def motion_params(motion, duration):
    if motion == "slow_zoom_out":
        return lambda t: 1.08 - 0.045 * (t / max(duration, 0.1))
    if motion in ["slow_zoom_in", "tiny_handheld", "gentle_pan_left", "gentle_pan_right"]:
        return lambda t: 1.0 + 0.055 * (t / max(duration, 0.1))
    return lambda t: 1.02


def animated_clip(frame_path, duration, motion):
    clip = ImageClip(str(frame_path)).set_duration(duration)
    zoom = motion_params(motion, duration)
    clip = clip.resize(lambda t: zoom(t))

    def pos(t):
        progress = t / max(duration, 0.1)
        base_x = (WIDTH - WIDTH * zoom(t)) / 2
        base_y = (HEIGHT - HEIGHT * zoom(t)) / 2
        if motion == "gentle_pan_left":
            return (base_x - 22 * progress, base_y)
        if motion == "gentle_pan_right":
            return (base_x + 22 * progress, base_y)
        if motion == "tiny_handheld":
            return (base_x + math.sin(t * 2.2) * 5, base_y + math.cos(t * 1.9) * 4)
        return (base_x, base_y)

    return clip.set_position(pos).on_color(size=(WIDTH, HEIGHT), color=(0, 0, 0), pos=("center", "center"))


def create_video(video_id, title, scene_payload):
    scenes = scene_payload.get("scenes", [])
    if not scenes:
        raise ValueError("No scenes found in scene_prompts.")
    character = scene_payload.get("character", {})
    char_desc = character.get("description", "")
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(video_id).strip() or "video")
    video_path = VIDEO_DIR / f"tiny_brave_tails_{safe_id}.mp4"
    clips = []
    voice_sources = []
    total_scenes = len(scenes)

    numeric_seed = sum(ord(ch) for ch in safe_id) % 100000
    for i, scene in enumerate(scenes, start=1):
        prompt = f"{char_desc}. {scene.get('image_prompt', '')} Emotion: {scene.get('emotion', 'peaceful')}."
        visual_path = VISUALS_DIR / f"visual_{safe_id}_{i:02d}.jpg"
        try:
            pollinations_image(prompt, visual_path, seed=numeric_seed * 100 + i)
            time.sleep(0.25)
        except Exception as exc:
            print(f"Image generation failed for scene {i}: {exc}")
            fallback_background(visual_path, scene.get("emotion", "peaceful"))

        audio_path, voice_source = create_scene_audio(scene, safe_id, i)
        voice_sources.append(voice_source)
        audio_clip = AudioFileClip(str(audio_path))
        pause_after = min(0.85, max(0.25, float(scene.get("pause_after", 0.35) or 0.35)))
        duration = max(4.0, audio_clip.duration + pause_after)
        frame_path = make_frame(safe_id, i, scene, title, visual_path, total_scenes)
        clip = animated_clip(frame_path, duration, scene.get("camera_motion", "slow_zoom_in")).set_audio(audio_clip)
        clips.append(clip)

    video = concatenate_videoclips(clips, method="compose")
    video.write_videofile(
        str(video_path),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="medium",
        threads=2,
        bitrate="9000k",
        ffmpeg_params=["-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-profile:v", "high"],
    )
    video.close()
    for clip in clips:
        try:
            if clip.audio:
                clip.audio.close()
            clip.close()
        except Exception:
            pass
    return video_path, ",".join(sorted(set(voice_sources)))


def main():
    client = get_sheets_client()
    spreadsheet = open_spreadsheet(client)
    content_sheet = get_worksheet(spreadsheet, CONTENT_SHEET_NAME)
    logs_sheet = get_worksheet(spreadsheet, LOGS_SHEET_NAME)
    values = get_all_values(content_sheet)
    headers = values[0]
    id_col = find_column(headers, "id")
    title_col = find_column(headers, "title")
    status_col = find_column(headers, "status")
    scene_prompts_col = find_column(headers, "scene_prompts")
    image_status_col = find_column(headers, "image_status")
    audio_status_col = find_column(headers, "audio_status")

    target_row_number, target_row = None, None
    for index, row in enumerate(values[1:], start=2):
        if get_cell(row, status_col).upper() == "GENERATED":
            target_row_number, target_row = index, row
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
    update_cell(content_sheet, target_row_number, status_col, "VIDEO_CREATED")
    update_cell(content_sheet, target_row_number, image_status_col, "CREATED")
    update_cell(content_sheet, target_row_number, audio_status_col, voice_source)
    log(logs_sheet, video_id, "GENERATE_VIDEO", f"Created long emotional video: {video_path}. Voice: {voice_source}")
    print(f"Video created: {video_path}")
    print(f"Voice source: {voice_source}")


if __name__ == "__main__":
    main()
