from pathlib import Path
import random
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
# Monkeypatch for moviepy 1.0.3 compatibility with Pillow 10+
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.LANCZOS
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips, CompositeVideoClip, TextClip, ColorClip
from config import VISUAL_DIR, VIDEO_DIR

WIDTH, HEIGHT = 1280, 720

MOOD_COLORS = {
    "calm": [(18, 30, 70), (50, 70, 130)],
    "happy": [(255, 200, 80), (80, 190, 120)],
    "bright": [(80, 180, 255), (255, 220, 80)],
    "educational": [(120, 200, 255), (255, 255, 180)],
    "adventure": [(30, 100, 70), (180, 120, 40)],
    "soft": [(25, 35, 75), (90, 80, 140)],
    "mixed": [(40, 80, 130), (130, 90, 160)],
}

def _font(size):
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except Exception:
        return ImageFont.load_default()

def _gradient_background(colors):
    img = Image.new("RGB", (WIDTH, HEIGHT), colors[0])
    pix = img.load()
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        r = int(colors[0][0] * (1-ratio) + colors[1][0] * ratio)
        g = int(colors[0][1] * (1-ratio) + colors[1][1] * ratio)
        b = int(colors[0][2] * (1-ratio) + colors[1][2] * ratio)
        for x in range(WIDTH):
            pix[x, y] = (r, g, b)
    return img

def _draw_simple_character(draw, character, x, y):
    colors = {
        "Benny": (230, 120, 40),
        "Luna": (245, 245, 245),
        "Milo": (130, 80, 45),
        "Coco": (120, 120, 130),
        "Olive": (120, 90, 50),
    }
    color = colors.get(character, (220, 180, 80))
    draw.ellipse((x, y, x+170, y+170), fill=color)
    draw.ellipse((x+45, y+55, x+70, y+80), fill=(0,0,0))
    draw.ellipse((x+100, y+55, x+125, y+80), fill=(0,0,0))
    draw.arc((x+55, y+80, x+115, y+130), 0, 180, fill=(0,0,0), width=4)
    draw.text((x-20, y+185), character, font=_font(28), fill=(255,255,255))

def make_scene_image(scene, video_type):
    VISUAL_DIR.mkdir(parents=True, exist_ok=True)
    mood = scene.get("mood", "calm")
    bg = _gradient_background(MOOD_COLORS.get(mood, MOOD_COLORS["calm"]))
    bg = bg.filter(ImageFilter.GaussianBlur(radius=0.2))
    draw = ImageDraw.Draw(bg)

    # simple moon/sun/stars
    for _ in range(45):
        x, y = random.randint(0, WIDTH), random.randint(0, HEIGHT//2)
        size = random.randint(1, 4)
        draw.ellipse((x, y, x+size, y+size), fill=(255,255,220))

    # hills
    draw.ellipse((-200, 460, 700, 900), fill=(25, 90, 60))
    draw.ellipse((450, 430, 1500, 920), fill=(30, 110, 70))

    _draw_simple_character(draw, scene.get("character", "Benny"), 545, 290)

    text = scene.get("text", "")[:90]
    draw.rounded_rectangle((80, 560, 1200, 690), radius=30, fill=(0,0,0,130))
    draw.text((110, 595), text, font=_font(34), fill=(255,255,255))

    path = VISUAL_DIR / f"{video_type}_scene_{scene['scene_number']:03d}.png"
    bg.save(path)
    return str(path)

def _particle_overlay(duration):
    # Transparent particle layer simulated with moving white dots.
    base = ColorClip((WIDTH, HEIGHT), color=(0,0,0)).set_opacity(0).set_duration(duration)
    return base

def render_video(story_data, audio_path, settings, video_type):
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    scene_duration = settings["scene_seconds"]
    clips = []

    for scene in story_data["scenes"]:
        image_path = make_scene_image(scene, video_type)
        zoom_direction = random.choice([1, -1])
        clip = (
            ImageClip(image_path)
            .set_duration(scene_duration)
            .resize(lambda t: 1 + (0.012 * t if zoom_direction == 1 else 0.012 * (scene_duration - t)))
            .set_position(("center", "center"))
        )
        clips.append(clip)

    video = concatenate_videoclips(clips, method="compose")

    audio = AudioFileClip(audio_path)
    final_duration = min(video.duration, audio.duration)
    video = video.subclip(0, final_duration)
    audio = audio.subclip(0, final_duration)
    final = video.set_audio(audio)

    output_path = VIDEO_DIR / f"{video_type}_final.mp4"
    final.write_videofile(str(output_path), fps=24, codec="libx264", audio_codec="aac")
    return str(output_path)
