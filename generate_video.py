import asyncio
import json
import math
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import quote_plus

import edge_tts
import requests
from moviepy.editor import AudioFileClip, CompositeAudioClip, ImageClip, concatenate_videoclips
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# MoviePy 1.0.3 compatibility with newer Pillow builds.
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.LANCZOS

from tbt_common import (
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
HUMAN_VOICE = os.getenv("EDGE_TTS_LONG_VOICE", os.getenv("EDGE_TTS_VOICE", "en-US-AvaNeural"))
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
ELEVENLABS_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2").strip()

EMOTION_STYLE = {
    "wonder": {"voice": HUMAN_VOICE, "rate": "-13%", "pitch": "+1Hz", "volume": "+0%"},
    "lonely": {"voice": HUMAN_VOICE, "rate": "-21%", "pitch": "-5Hz", "volume": "-1%"},
    "worried": {"voice": HUMAN_VOICE, "rate": "-18%", "pitch": "-4Hz", "volume": "+0%"},
    "afraid": {"voice": HUMAN_VOICE, "rate": "-16%", "pitch": "-6Hz", "volume": "+0%"},
    "brave": {"voice": HUMAN_VOICE, "rate": "-11%", "pitch": "-1Hz", "volume": "+1%"},
    "relieved": {"voice": HUMAN_VOICE, "rate": "-15%", "pitch": "-2Hz", "volume": "+0%"},
    "peaceful": {"voice": HUMAN_VOICE, "rate": "-19%", "pitch": "-4Hz", "volume": "-1%"},
}


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
    heights = [draw.textbbox((0, 0), line, font=font)[3] - draw.textbbox((0, 0), line, font=font)[1] for line in lines]
    total_h = sum(heights) + spacing * max(0, len(lines) - 1)
    y = center_y - total_h // 2
    for line, h in zip(lines, heights):
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (WIDTH - (bbox[2] - bbox[0])) // 2
        draw.text((x + 4, y + 4), line, font=font, fill=(0, 0, 0, 220))
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
    top, bottom = palettes.get(str(emotion).lower(), palettes["peaceful"])
    img = Image.new("RGB", (WIDTH, HEIGHT), top)
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        color = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        draw.line([(0, y), (WIDTH, y)], fill=color)
    draw.ellipse((760, 130, 940, 310), fill=(250, 230, 170))
    for i in range(80):
        x = (i * 137) % WIDTH
        y = 70 + (i * 83) % 820
        r = 1 + (i % 3)
        draw.ellipse((x, y, x + r, y + r), fill=(255, 245, 205))
    img.save(output_path, quality=95)
    return output_path


def pollinations_image(prompt, output_path, seed):
    final_prompt = f"""
Premium cinematic animated movie still, beautiful vertical 9:16 frame,
2D storybook / Pixar-like emotional composition, dramatic camera angle,
soft volumetric moonlight, warm rim light, expressive animal eyes, detailed environment,
depth of field, painterly texture, consistent character design, high quality, no text, no watermark, no logo.
Make this a specific scene, not a generic cute animal image. Exact moment: {prompt}
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
    rgba = img.convert("RGBA")
    # Very light overlay only. Do not bury the picture under black boxes.
    top_grad = Image.new("RGBA", (WIDTH, 260), (0, 0, 0, 75))
    bottom_grad = Image.new("RGBA", (WIDTH, 360), (0, 0, 0, 95))
    rgba.alpha_composite(top_grad, (0, 0))
    rgba.alpha_composite(bottom_grad, (0, HEIGHT - 360))
    return rgba


def make_frame(video_id, shot_index, shot, title, image_path, total_shots):
    bg = prepare_background(image_path)
    draw = ImageDraw.Draw(bg)
    brand_font = load_font(40, True)
    title_font = load_font(28, False)
    sub_font = load_font(42, True)
    small_font = load_font(26, False)

    draw.text((50, 34), "Tiny Brave Tails", font=brand_font, fill=(255, 238, 190, 255))
    y = 92
    for line in wrap_ltr(draw, title, title_font, 940, 2):
        draw.text((50, y), line, font=title_font, fill=(245, 245, 245, 235))
        y += 36

    subtitle = os.getenv("SHOW_SUBTITLES", "true").lower() not in {"0", "false", "no"} and (shot.get("subtitle_en") or shot.get("narration_en", "")) or ""
    lines = wrap_ltr(draw, subtitle, sub_font, 930, 3)
    draw_centered_lines(draw, lines, sub_font, HEIGHT - 210, (255, 255, 255, 255), 8)

    bar_x, bar_y, bar_w, bar_h = 120, HEIGHT - 72, 840, 10
    draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), radius=8, fill=(255, 255, 255, 75))
    draw.rounded_rectangle((bar_x, bar_y, bar_x + int(bar_w * shot_index / max(total_shots, 1)), bar_y + bar_h), radius=8, fill=(255, 232, 170, 245))
    cta = "Soft animal stories with tiny courage"
    bbox = draw.textbbox((0, 0), cta, font=small_font)
    draw.text(((WIDTH - (bbox[2] - bbox[0])) // 2, HEIGHT - 48), cta, font=small_font, fill=(255, 255, 255, 220))

    frame_path = FRAMES_DIR / f"frame_{video_id}_{shot_index:03d}.jpg"
    bg.convert("RGB").save(frame_path, quality=95)
    return frame_path


def humanize_text(text):
    clean = re.sub(r"\s+", " ", str(text or "").replace("\n", " ")).strip()
    if not clean:
        raise ValueError("Empty narration text")
    # Human-like rhythm: emotional pauses without over-slow robotic dragging.
    replacements = {
        r"\bbut\b": "but...",
        r"\band then\b": "and then...",
        r"\bfor a moment\b": "for a moment...",
        r"\bstill\b": "still...",
        r"\bsuddenly\b": "suddenly...",
        r"\bhe whispered\b": "he whispered...",
        r"\bshe whispered\b": "she whispered...",
    }
    for pattern, repl in replacements.items():
        clean = re.sub(pattern, repl, clean, flags=re.IGNORECASE)
    clean = re.sub(r"([.!?])\s+", r"\1 ", clean)
    clean = re.sub(r"\.{4,}", "...", clean)
    return clean



def create_elevenlabs_audio(text, output_path, emotion="peaceful"):
    if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
        raise RuntimeError("ELEVENLABS_API_KEY or ELEVENLABS_VOICE_ID not set")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    payload = {
        "text": humanize_text(text),
        "model_id": ELEVENLABS_MODEL_ID,
        "voice_settings": {
            "stability": 0.42,
            "similarity_boost": 0.82,
            "style": 0.38,
            "use_speaker_boost": True,
        },
    }
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "accept": "audio/mpeg",
        "content-type": "application/json",
    }
    response = requests.post(url, json=payload, headers=headers, timeout=180)
    response.raise_for_status()
    output_path.write_bytes(response.content)
    return output_path

async def create_edge_audio_async(text, output_path, emotion="peaceful"):
    style = EMOTION_STYLE.get(str(emotion).lower(), EMOTION_STYLE["peaceful"])
    communicate = edge_tts.Communicate(
        text=humanize_text(text),
        voice=style["voice"],
        rate=style["rate"],
        pitch=style["pitch"],
        volume=style["volume"],
    )
    await communicate.save(str(output_path))


def create_edge_audio(text, output_path, emotion="peaceful"):
    asyncio.run(create_edge_audio_async(text, output_path, emotion))
    return output_path


def create_espeak_audio(text, output_path):
    command = ["espeak-ng", "-v", "en-us", "-s", "118", "-p", "35", "-a", "145", "-w", str(output_path), humanize_text(text)]
    subprocess.run(command, check=True)
    return output_path


def normalize_audio(input_path, video_id, shot_index):
    normalized = AUDIO_DIR / f"audio_{video_id}_{shot_index:03d}_norm.m4a"
    command = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-af", "loudnorm=I=-18:TP=-1.5:LRA=9,acompressor=threshold=-22dB:ratio=2.2:attack=20:release=250",
        "-ar", "48000", "-ac", "2", "-c:a", "aac", "-b:a", "192k",
        str(normalized),
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return normalized
    except Exception as exc:
        print(f"Audio normalization failed, using original audio: {exc}")
        return input_path


def create_shot_audio(shot, video_id, shot_index):
    narration = shot.get("narration_en", "").strip()
    emotion = shot.get("emotion", "peaceful").strip().lower()
    mp3_path = AUDIO_DIR / f"audio_{video_id}_{shot_index:03d}.mp3"
    wav_path = AUDIO_DIR / f"audio_{video_id}_{shot_index:03d}.wav"
    try:
        if ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID:
            create_elevenlabs_audio(narration, mp3_path, emotion)
            return normalize_audio(mp3_path, video_id, shot_index), f"elevenlabs:{ELEVENLABS_VOICE_ID}:human"
    except Exception as exc:
        print(f"ElevenLabs failed for shot {shot_index}, falling back to Edge: {exc}")
    try:
        create_edge_audio(narration, mp3_path, emotion)
        return normalize_audio(mp3_path, video_id, shot_index), f"edge-tts:{EMOTION_STYLE.get(emotion, EMOTION_STYLE['peaceful'])['voice']}:cinematic-free"
    except Exception as exc:
        print(f"Edge TTS failed for shot {shot_index}: {exc}")
        create_espeak_audio(narration, wav_path)
        return normalize_audio(wav_path, video_id, shot_index), "espeak-ng:fallback"


def motion_params(motion, duration):
    if motion == "slow_zoom_out":
        return lambda t: 1.09 - 0.055 * (t / max(duration, 0.1))
    if motion in ["slow_zoom_in", "tiny_handheld", "gentle_pan_left", "gentle_pan_right"]:
        return lambda t: 1.0 + 0.07 * (t / max(duration, 0.1))
    return lambda t: 1.03


def animated_clip(frame_path, duration, motion):
    clip = ImageClip(str(frame_path)).set_duration(duration)
    zoom = motion_params(motion, duration)
    clip = clip.resize(lambda t: zoom(t))

    def pos(t):
        progress = t / max(duration, 0.1)
        base_x = (WIDTH - WIDTH * zoom(t)) / 2
        base_y = (HEIGHT - HEIGHT * zoom(t)) / 2
        if motion == "gentle_pan_left":
            return (base_x - 28 * progress, base_y)
        if motion == "gentle_pan_right":
            return (base_x + 28 * progress, base_y)
        if motion == "tiny_handheld":
            return (base_x + math.sin(t * 2.0) * 4, base_y + math.cos(t * 1.7) * 3)
        return (base_x, base_y)

    return clip.set_position(pos).on_color(size=(WIDTH, HEIGHT), color=(0, 0, 0), pos=("center", "center"))


def split_scene_to_shots(scene):
    if isinstance(scene.get("shots"), list) and scene["shots"]:
        return scene["shots"][:4]
    narration = scene.get("narration_en", "")
    parts = [x.strip() for x in re.split(r"(?<=[.!?])\s+", narration) if x.strip()]
    if len(parts) < 4:
        parts = [narration or "Toby listened to the quiet forest.", "The moon made every shadow feel alive.", "His tiny feet touched the wet ground.", "He moved forward anyway."]
    motions = ["slow_zoom_in", "gentle_pan_left", "tiny_handheld", "slow_zoom_out"]
    return [
        {
            "shot_number": i + 1,
            "emotion": scene.get("emotion", "peaceful"),
            "narration_en": part,
            "subtitle_en": part,
            "image_prompt": f"{scene.get('image_prompt', '')}. Exact visual moment: {part}",
            "camera_motion": motions[i % len(motions)],
            "pause_after": 0.25,
        }
        for i, part in enumerate(parts[:4])
    ]


def flatten_story(scene_payload):
    shots = []
    character = scene_payload.get("character", {})
    char_desc = character.get("description", "")
    for scene_index, scene in enumerate(scene_payload.get("scenes", []), start=1):
        scene_shots = split_scene_to_shots(scene)
        for shot in scene_shots:
            prompt = shot.get("image_prompt") or scene.get("image_prompt", "")
            if char_desc and char_desc[:40].lower() not in str(prompt).lower():
                prompt = f"{char_desc}. {prompt}"
            shots.append({
                "scene_number": scene_index,
                "shot_number": shot.get("shot_number", len(shots) + 1),
                "emotion": shot.get("emotion", scene.get("emotion", "peaceful")),
                "narration_en": shot.get("narration_en") or scene.get("narration_en", ""),
                "subtitle_en": shot.get("subtitle_en") or shot.get("narration_en") or scene.get("subtitle_en", ""),
                "image_prompt": prompt,
                "camera_motion": shot.get("camera_motion") or scene.get("camera_motion", "slow_zoom_in"),
                "pause_after": shot.get("pause_after", 0.25),
            })
    return shots


def create_video(video_id, title, scene_payload):
    shots = flatten_story(scene_payload)
    if not shots:
        raise ValueError("No scenes/shots found in scene_prompts.")
    min_required_shots = 12 if len(shots) >= 12 else 4
    if len(shots) < min_required_shots:
        raise ValueError(f"Too few cinematic shots found: {len(shots)}. Reset the row to IDEA and regenerate the story.")
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(video_id).strip() or "video")
    video_path = VIDEO_DIR / f"tiny_brave_tails_{safe_id}.mp4"
    clips = []
    voice_sources = []
    total_shots = len(shots)
    numeric_seed = sum(ord(ch) for ch in safe_id) % 100000

    for i, shot in enumerate(shots, start=1):
        prompt = f"{shot.get('image_prompt', '')} Emotion: {shot.get('emotion', 'peaceful')}."
        visual_path = VISUALS_DIR / f"visual_{safe_id}_{i:03d}.jpg"
        try:
            pollinations_image(prompt, visual_path, seed=numeric_seed * 1000 + i)
            time.sleep(0.2)
        except Exception as exc:
            raise RuntimeError(f"Image generation failed for shot {i}. Refusing to create a bad video without real pictures: {exc}") from exc

        audio_path, voice_source = create_shot_audio(shot, safe_id, i)
        voice_sources.append(voice_source)
        audio_clip = AudioFileClip(str(audio_path))
        pause_after = min(0.6, max(0.15, float(shot.get("pause_after", 0.25) or 0.25)))
        duration = max(3.0, audio_clip.duration + pause_after)
        frame_path = make_frame(safe_id, i, shot, title, visual_path, total_shots)
        clip = animated_clip(frame_path, duration, shot.get("camera_motion", "slow_zoom_in")).set_audio(audio_clip)
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
    return video_path, ",".join(sorted(set(voice_sources))) + f" | shots={total_shots}"


def main():
    client = get_sheets_client()
    spreadsheet = open_spreadsheet(client)
    content_sheet = get_worksheet(spreadsheet, CONTENT_SHEET_NAME)
    logs_sheet = get_logs_worksheet(spreadsheet)
    values = get_all_values(content_sheet)
    headers = values[0]
    id_col = find_column(headers, "id")
    title_col = find_column(headers, "title")
    status_col = find_column(headers, "status")
    scene_prompts_col = find_column(headers, "scene_prompts")
    image_status_col = find_column(headers, "image_status")
    audio_status_col = find_column(headers, "audio_status")
    video_type_col = find_optional_column(headers, "video_type")
    error_message_col = find_optional_column(headers, "error_message")
    requested_video_type = (os.getenv("TBT_VIDEO_TYPE", "") or "").strip().lower().replace("-", "_").replace(" ", "_")

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

    video_id = get_cell(target_row, id_col)
    title = get_cell(target_row, title_col)
    scene_raw = get_cell(target_row, scene_prompts_col)
    if not title or not scene_raw:
        raise ValueError("Missing title or scene_prompts.")
    scene_payload = json.loads(scene_raw)
    try:
        video_path, voice_source = create_video(video_id, title, scene_payload)
    except Exception as exc:
        if error_message_col:
            update_cell(content_sheet, target_row_number, error_message_col, str(exc)[:1500])
        log(logs_sheet, video_id, "GENERATE_VIDEO_ERROR", str(exc))
        raise
    update_cell(content_sheet, target_row_number, status_col, "VIDEO_CREATED")
    update_cell(content_sheet, target_row_number, image_status_col, "CREATED")
    update_cell(content_sheet, target_row_number, audio_status_col, voice_source)
    if error_message_col:
        update_cell(content_sheet, target_row_number, error_message_col, "")
    log(logs_sheet, video_id, "GENERATE_VIDEO", f"Created video with one picture per shot: {video_path}. Voice: {voice_source}")
    print(f"Video created: {video_path}")
    print(f"Voice source: {voice_source}")


if __name__ == "__main__":
    main()
