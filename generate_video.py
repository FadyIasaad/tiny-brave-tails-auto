import os
import json
import re
import subprocess
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import requests
import gspread
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips
from google.oauth2.service_account import Credentials


SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "")

CONTENT_SHEET_NAME = "Content"
LOGS_SHEET_NAME = "Logs"

OUTPUT_DIR = Path("output")
VISUALS_DIR = OUTPUT_DIR / "visuals"
OUTPUT_DIR.mkdir(exist_ok=True)
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


def clean_query(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    remove_words = {
        "emotional", "storybook", "illustration", "cinematic", "lighting",
        "vertical", "youtube", "shorts", "style", "warm", "soft",
        "detailed", "expressive", "animal", "emotion", "family", "friendly",
        "text", "image", "prompt", "scene", "composition", "child", "safe",
        "appealing", "adults", "kids", "no", "logos", "watermark",
    }

    words = [w for w in text.split() if w not in remove_words and len(w) > 2]
    return " ".join(words[:8]).strip()


def build_queries(scene, animal, topic):
    scene_text = scene.get("text", "")
    image_prompt = scene.get("image_prompt", "")

    cleaned_prompt = clean_query(image_prompt)
    cleaned_scene = clean_query(scene_text)
    cleaned_topic = clean_query(topic)

    queries = []

    if animal and cleaned_scene:
        queries.append(f"{animal} {cleaned_scene}")

    if animal and cleaned_prompt:
        queries.append(f"{animal} {cleaned_prompt}")

    if animal and cleaned_topic:
        queries.append(f"{animal} {cleaned_topic}")

    if animal:
        queries.append(f"{animal} cute")
        queries.append(f"{animal} sad")
        queries.append(f"{animal} close up")
        queries.append(animal)

    final = []
    seen = set()

    for q in queries:
        q = re.sub(r"\s+", " ", q).strip()
        if q and q not in seen:
            final.append(q)
            seen.add(q)

    return final


def download_file(url, output_path):
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    output_path.write_bytes(response.content)
    return output_path


def search_pexels_photo(query):
    if not PEXELS_API_KEY:
        return None

    url = "https://api.pexels.com/v1/search"
    headers = {"Authorization": PEXELS_API_KEY}
    params = {
        "query": query,
        "orientation": "portrait",
        "per_page": 10,
    }

    response = requests.get(url, headers=headers, params=params, timeout=45)
    response.raise_for_status()

    data = response.json()
    photos = data.get("photos", [])

    if not photos:
        return None

    best = sorted(
        photos,
        key=lambda p: abs((p.get("width", 1) / max(p.get("height", 1), 1)) - 0.5625),
    )[0]

    src = best.get("src", {})
    return src.get("large2x") or src.get("large") or src.get("original")


def search_pixabay_image(query):
    if not PIXABAY_API_KEY:
        return None

    url = "https://pixabay.com/api/"
    params = {
        "key": PIXABAY_API_KEY,
        "q": query,
        "image_type": "photo",
        "orientation": "vertical",
        "safesearch": "true",
        "per_page": 10,
    }

    response = requests.get(url, params=params, timeout=45)
    response.raise_for_status()

    data = response.json()
    hits = data.get("hits", [])

    if not hits:
        return None

    best = sorted(
        hits,
        key=lambda p: abs((p.get("imageWidth", 1) / max(p.get("imageHeight", 1), 1)) - 0.5625),
    )[0]

    return best.get("largeImageURL") or best.get("webformatURL")


def fetch_visual_for_scene(scene, animal, topic, output_path):
    queries = build_queries(scene, animal, topic)
    last_error = None

    for query in queries:
        try:
            image_url = search_pexels_photo(query)
            if image_url:
                download_file(image_url, output_path)
                return {"source": "pexels", "query": query, "path": str(output_path)}
        except Exception as e:
            last_error = f"Pexels error for '{query}': {e}"

    for query in queries:
        try:
            image_url = search_pixabay_image(query)
            if image_url:
                download_file(image_url, output_path)
                return {"source": "pixabay", "query": query, "path": str(output_path)}
        except Exception as e:
            last_error = f"Pixabay error for '{query}': {e}"

    raise ValueError(f"No visual found. Last error: {last_error}. Queries tried: {queries}")


def fallback_gradient_frame():
    image = Image.new("RGB", (WIDTH, HEIGHT), "#101820")
    draw = ImageDraw.Draw(image)

    for y in range(HEIGHT):
        ratio = y / HEIGHT
        r = int(12 + ratio * 18)
        g = int(22 + ratio * 28)
        b = int(34 + ratio * 38)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))

    return image


def prepare_background(image_path):
    try:
        img = Image.open(image_path).convert("RGB")
    except Exception:
        return fallback_gradient_frame()

    img = ImageOps.exif_transpose(img)

    target_ratio = WIDTH / HEIGHT
    img_ratio = img.width / img.height

    if img_ratio > target_ratio:
        new_height = HEIGHT
        new_width = int(new_height * img_ratio)
    else:
        new_width = WIDTH
        new_height = int(new_width / img_ratio)

    img = img.resize((new_width, new_height), Image.LANCZOS)

    left = (new_width - WIDTH) // 2
    top = (new_height - HEIGHT) // 2
    img = img.crop((left, top, left + WIDTH, top + HEIGHT))

    # cinematic dark overlay for text readability
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 95))
    img = Image.alpha_composite(img.convert("RGBA"), overlay)

    # slight blur at edges feel
    return img.convert("RGB")


def load_font(size):
    possible_fonts = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    for font_path in possible_fonts:
        if Path(font_path).exists():
            return ImageFont.truetype(font_path, size)

    return ImageFont.load_default()


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

        # thick shadow
        draw.text((x + 5, current_y + 5), line, font=font, fill=(0, 0, 0, 210))
        draw.text((x, current_y), line, font=font, fill=fill)

        current_y += line_height + line_spacing


def create_frame(text, title, video_id, segment_index, total_segments, image_path):
    img = prepare_background(image_path).convert("RGBA")
    draw = ImageDraw.Draw(img)

    title_font = load_font(48)
    body_font = load_font(72)
    small_font = load_font(34)

    # Top gradient box
    top_overlay = Image.new("RGBA", (WIDTH, 260), (0, 0, 0, 115))
    img.alpha_composite(top_overlay, (0, 0))

    draw = ImageDraw.Draw(img)

    brand = "Tiny Brave Tails"
    draw.text((60, 70), brand, font=title_font, fill=(255, 235, 190, 255))

    wrapped_title = textwrap.shorten(title, width=34, placeholder="...")
    draw.text((60, 145), wrapped_title, font=small_font, fill=(235, 240, 245, 235))

    # Caption background
    caption_box = Image.new("RGBA", (WIDTH - 120, 620), (0, 0, 0, 115))
    caption_box = caption_box.filter(ImageFilter.GaussianBlur(1))
    img.alpha_composite(caption_box, (60, 650))

    draw = ImageDraw.Draw(img)

    draw_centered_multiline(
        draw=draw,
        text=text,
        font=body_font,
        y=960,
        fill=(255, 255, 255, 255),
        max_width=900,
        line_spacing=20,
    )

    # Progress bar
    bar_x = 120
    bar_y = 1700
    bar_w = 840
    bar_h = 12
    progress = (segment_index + 1) / total_segments

    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + bar_w, bar_y + bar_h),
        radius=8,
        fill=(255, 255, 255, 60),
    )
    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + int(bar_w * progress), bar_y + bar_h),
        radius=8,
        fill=(255, 235, 190, 235),
    )

    cta = "Follow for tiny stories with big lessons"
    bbox = draw.textbbox((0, 0), cta, font=small_font)
    draw.text(
        ((WIDTH - (bbox[2] - bbox[0])) // 2, 1760),
        cta,
        font=small_font,
        fill=(255, 255, 255, 235),
    )

    frame_path = OUTPUT_DIR / f"frame_{video_id}_{segment_index:02d}.jpg"
    img.convert("RGB").save(frame_path, quality=95)
    return frame_path


def clean_text_for_tts(text):
    text = text.replace("\n", " ")
    text = re.sub(r"[“”]", '"', text)
    text = re.sub(r"[‘’]", "'", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def create_voice_with_gtts(script, output_audio):
    clean_script = clean_text_for_tts(script)
    tts = gTTS(text=clean_script, lang="en", slow=False, tld="com")
    tts.save(str(output_audio))


def create_voice_with_espeak(script, output_audio):
    clean_script = clean_text_for_tts(script)
    command = [
        "espeak-ng",
        "-v",
        "en-us",
        "-s",
        "145",
        "-p",
        "45",
        "-a",
        "170",
        "-w",
        str(output_audio),
        clean_script,
    ]
    subprocess.run(command, check=True)


def create_voice(script, safe_id):
    mp3_path = OUTPUT_DIR / f"voice_{safe_id}.mp3"
    wav_path = OUTPUT_DIR / f"voice_{safe_id}.wav"

    try:
        create_voice_with_gtts(script, mp3_path)
        return mp3_path, "gTTS"
    except Exception as e:
        print(f"gTTS failed, falling back to espeak-ng: {e}")
        create_voice_with_espeak(script, wav_path)
        return wav_path, "espeak-ng"


def split_script_by_scenes(script, scenes):
    scene_texts = []

    if isinstance(scenes, list) and scenes:
        for scene in scenes:
            text = str(scene.get("text", "")).strip()
            if text:
                scene_texts.append(text)

    if len(scene_texts) == 3:
        return scene_texts

    # fallback sentence chunking
    script = script.replace("\n", " ").strip()
    parts = re.split(r"(?<=[.!?])\s+", script)
    parts = [p.strip() for p in parts if p.strip()]

    if len(parts) <= 3:
        return parts

    chunk_size = max(1, len(parts) // 3)
    chunks = [
        " ".join(parts[:chunk_size]),
        " ".join(parts[chunk_size:chunk_size * 2]),
        " ".join(parts[chunk_size * 2:]),
    ]
    return [c for c in chunks if c.strip()]


def create_video(video_id, title, script, scenes, animal, topic):
    safe_id = str(video_id).strip() or "video"
    video_path = OUTPUT_DIR / f"tiny_brave_tails_{safe_id}.mp4"

    audio_path, voice_source = create_voice(script, safe_id)
    audio_clip = AudioFileClip(str(audio_path))
    audio_duration = audio_clip.duration

    video_visual_dir = VISUALS_DIR / safe_id
    video_visual_dir.mkdir(parents=True, exist_ok=True)

    visual_paths = []
    fetch_results = []

    for i, scene in enumerate(scenes, start=1):
        output_path = video_visual_dir / f"scene_{i}.jpg"
        try:
            result = fetch_visual_for_scene(scene, animal, topic, output_path)
            fetch_results.append(result)
            visual_paths.append(output_path)
        except Exception as e:
            print(f"Visual fetch failed for scene {i}: {e}")
            fallback_path = video_visual_dir / f"fallback_{i}.jpg"
            fallback_gradient_frame().save(fallback_path, quality=95)
            visual_paths.append(fallback_path)
            fetch_results.append({"source": "fallback", "query": "", "path": str(fallback_path)})

    chunks = split_script_by_scenes(script, scenes)
    total_segments = min(len(chunks), len(visual_paths))

    if total_segments == 0:
        raise ValueError("No script chunks available for video creation.")

    chunks = chunks[:total_segments]
    visual_paths = visual_paths[:total_segments]

    total_chars = sum(len(chunk) for chunk in chunks)
    clips = []

    for i, chunk in enumerate(chunks):
        if total_chars > 0:
            duration = max(3.0, audio_duration * (len(chunk) / total_chars))
        else:
            duration = audio_duration / total_segments

        frame_path = create_frame(
            text=chunk,
            title=title,
            video_id=safe_id,
            segment_index=i,
            total_segments=total_segments,
            image_path=visual_paths[i],
        )

        # subtle zoom-in effect
        clip = (
            ImageClip(str(frame_path))
            .set_duration(duration)
            .resize(lambda t: 1 + 0.025 * (t / max(duration, 0.1)))
            .set_position("center")
        )

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
        bitrate="4500k",
    )

    audio_clip.close()
    video.close()

    return video_path, voice_source, fetch_results


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
    topic_col = find_column(headers, "topic")
    animal_col = find_column(headers, "animal")
    script_col = find_column(headers, "script")
    title_col = find_column(headers, "title")
    status_col = find_column(headers, "status")
    scene_prompts_col = find_column(headers, "scene_prompts")
    image_status_col = find_column(headers, "image_status")
    audio_status_col = find_column(headers, "audio_status")

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
    topic = get_cell(target_row, topic_col)
    animal = get_cell(target_row, animal_col)
    script = get_cell(target_row, script_col)
    title = get_cell(target_row, title_col)
    scene_prompts_raw = get_cell(target_row, scene_prompts_col)

    if not script or not title:
        raise ValueError(f"Missing script/title in row {target_row_number}")

    if not scene_prompts_raw:
        raise ValueError(f"Missing scene_prompts in row {target_row_number}")

    scenes = json.loads(scene_prompts_raw)

    if not isinstance(scenes, list) or len(scenes) != 3:
        raise ValueError("scene_prompts must contain exactly 3 scenes.")

    video_path, voice_source, fetch_results = create_video(
        video_id=video_id,
        title=title,
        script=script,
        scenes=scenes,
        animal=animal,
        topic=topic,
    )

    content_sheet.update_cell(target_row_number, status_col, "VIDEO_CREATED")
    content_sheet.update_cell(target_row_number, image_status_col, "CREATED")
    content_sheet.update_cell(target_row_number, audio_status_col, voice_source)

    log(
        logs_sheet,
        video_id,
        "GENERATE_VIDEO",
        f"Video created for row {target_row_number}: {video_path}. Voice: {voice_source}. Visuals: {json.dumps(fetch_results)}",
    )

    print(f"Video created: {video_path}")
    print(f"Voice source: {voice_source}")
    print(json.dumps(fetch_results, indent=2))


if __name__ == "__main__":
    main()
