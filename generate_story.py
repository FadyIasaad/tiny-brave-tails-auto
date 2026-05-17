import json
import os
import re
import unicodedata
from datetime import datetime, timezone

import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

CONTENT_SHEET_NAME = "Content"
LOGS_SHEET_NAME = "Logs"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

ARABIC_LETTERS_RE = re.compile(r"[\u0600-\u06FF]")
ARABIC_DIACRITICS_RE = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
DIRECTION_MARKS_RE = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def get_sheets_client():
    service_account_info = json.loads(SERVICE_ACCOUNT_JSON)
    credentials = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
    return gspread.authorize(credentials)


def clean_json_response(text):
    text = str(text or "").strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON object found in Gemini response: {text}")

    return text[start : end + 1]


def clean_arabic_subtitle(text):
    text = str(text or "")
    text = unicodedata.normalize("NFC", text)
    text = DIRECTION_MARKS_RE.sub("", text)
    text = text.replace("ـ", "")
    text = ARABIC_DIACRITICS_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def validate_arabic_subtitle(text, scene_number):
    cleaned = clean_arabic_subtitle(text)

    if not cleaned:
        raise ValueError(f"Missing Arabic subtitle in scene {scene_number}.")

    arabic_chars = ARABIC_LETTERS_RE.findall(cleaned)
    if len(arabic_chars) < 8:
        raise ValueError(f"Arabic subtitle is too weak or not Arabic in scene {scene_number}: {cleaned}")

    bad_phrases = [
        "ترجمة عربية",
        "نفس الكلام",
        "subtitle",
        "translation",
        "arabic",
    ]
    lowered = cleaned.lower()
    if any(phrase.lower() in lowered for phrase in bad_phrases):
        raise ValueError(f"Arabic subtitle looks like a placeholder in scene {scene_number}: {cleaned}")

    return cleaned


def build_prompt(topic, animal, lesson):
    return f"""
You are the lead writer and Arabic subtitle translator for a family-friendly YouTube Shorts channel called Tiny Brave Tails.

Channel style:
Warm emotional 2D cartoon storybook animal stories with simple life lessons.

Create one short animated story package.

Input:
Topic: {topic}
Animal: {animal}
Life lesson: {lesson}

Core rules:
- English narration only in narration_en.
- English subtitle in subtitle_en must match narration_en.
- Arabic subtitle in subtitle_ar must be a natural full Modern Standard Arabic translation of narration_en.
- Do not transliterate English words into Arabic.
- Do not output reversed Arabic.
- Do not output broken Arabic letters.
- Do not output Arabic presentation forms.
- Do not add strange symbols, hidden direction marks, or decoration marks.
- Arabic must be normal readable Arabic like children's storybooks.
- Each subtitle_ar must be complete, clear, emotional, and faithful to the English narration.
- Family-friendly.
- No horror.
- No gore.
- No explicit violence.
- No claim that the story is true.
- Strong hook in scene 1.
- Fast pacing.
- Emotional escalation.
- 35 to 55 seconds total.
- Cute 2D storybook style.
- Return valid JSON only. No markdown. No explanation.

Arabic quality example:
English: "Little Pip, a tiny puppy, sat alone as the first rain began to fall."
Good Arabic: "جلس بيب الصغير، الجرو اللطيف، وحيدا بينما بدأت أول قطرات المطر تتساقط."
Bad Arabic is forbidden: broken words, random letters, reversed Arabic, machine-like nonsense, or placeholders.

Return exactly this JSON shape:
{{
  "title": "Short emotional YouTube title under 70 characters",
  "description": "Short YouTube description with hashtags",
  "character": {{
    "name": "Character name",
    "description": "Consistent 2D storybook character design. Include animal type, color, eyes, accessory, mood, and visual style."
  }},
  "scenes": [
    {{
      "scene_number": 1,
      "narration_en": "English narration for this scene. This text will be spoken.",
      "subtitle_en": "Same meaning as narration_en, short and complete.",
      "subtitle_ar": "ترجمة عربية طبيعية وكاملة لنفس الكلام",
      "image_prompt": "Vertical 9:16 warm 2D cartoon storybook frame matching this scene, no text"
    }}
  ]
}}

Scene rules:
- Exactly 6 scenes.
- Scene 1: strong emotional hook.
- Scene 2: introduce the problem.
- Scene 3: fear or sadness.
- Scene 4: brave action.
- Scene 5: emotional turn.
- Scene 6: life lesson.
- Each narration_en should be 1 or 2 short sentences.
- Each subtitle_en should be short enough to fit clearly on screen.
- Each subtitle_ar must be complete, not summarized.
- Keep the same character design in all image prompts.
- Each image_prompt must include: warm 2D cartoon storybook, soft colors, expressive animal face, child-safe, vertical 9:16, no text, no watermark.
"""


def generate_story_package(topic, animal, lesson):
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.5-flash")

    prompt = build_prompt(topic, animal, lesson)

    response = model.generate_content(
        prompt,
        generation_config={
            "temperature": 0.75,
        },
    )

    data = json.loads(clean_json_response(response.text))

    for key in ["title", "description", "character", "scenes"]:
        if key not in data:
            raise ValueError(f"Missing key: {key}")

    scenes = data["scenes"]
    if not isinstance(scenes, list) or len(scenes) != 6:
        raise ValueError("Expected exactly 6 scenes.")

    cleaned_scenes = []
    for i, scene in enumerate(scenes, start=1):
        narration = str(scene.get("narration_en", "")).strip()
        subtitle_en = str(scene.get("subtitle_en", "")).strip() or narration
        subtitle_ar = validate_arabic_subtitle(scene.get("subtitle_ar", ""), i)
        image_prompt = str(scene.get("image_prompt", "")).strip()

        if not narration or not subtitle_en or not image_prompt:
            raise ValueError(f"Incomplete scene {i}: {scene}")

        cleaned_scenes.append(
            {
                "scene_number": i,
                "narration_en": narration,
                "subtitle_en": subtitle_en,
                "subtitle_ar": subtitle_ar,
                "image_prompt": image_prompt,
            }
        )

    data["title"] = str(data["title"]).strip()
    data["description"] = str(data["description"]).strip()
    data["scenes"] = cleaned_scenes
    data["script"] = " ".join(scene["narration_en"] for scene in cleaned_scenes)

    return data


def find_column(headers, name):
    if name not in headers:
        raise ValueError(f"Missing required column: {name}")
    return headers.index(name) + 1


def get_cell(row, col):
    return row[col - 1].strip() if len(row) >= col else ""


def log(logs_sheet, video_id, action, message):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    logs_sheet.append_row([now, video_id, action, message], value_input_option="USER_ENTERED")


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
    topic_col = find_column(headers, "topic")
    animal_col = find_column(headers, "animal")
    lesson_col = find_column(headers, "lesson")
    script_col = find_column(headers, "script")
    title_col = find_column(headers, "title")
    description_col = find_column(headers, "description")
    status_col = find_column(headers, "status")
    created_at_col = find_column(headers, "created_at")
    scene_prompts_col = find_column(headers, "scene_prompts")
    image_status_col = find_column(headers, "image_status")
    audio_status_col = find_column(headers, "audio_status")
    youtube_status_col = find_column(headers, "youtube_status")
    youtube_video_id_col = find_column(headers, "youtube_video_id")

    target_row_number = None
    target_row = None

    for index, row in enumerate(values[1:], start=2):
        if get_cell(row, status_col) == "IDEA":
            target_row_number = index
            target_row = row
            break

    if target_row_number is None:
        log(logs_sheet, "", "GENERATE_STORY", "No IDEA row found.")
        print("No IDEA row found.")
        return

    video_id = get_cell(target_row, id_col)
    topic = get_cell(target_row, topic_col)
    animal = get_cell(target_row, animal_col)
    lesson = get_cell(target_row, lesson_col)

    try:
        package = generate_story_package(topic, animal, lesson)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        scene_payload = {
            "character": package["character"],
            "scenes": package["scenes"],
        }

        content_sheet.update_cell(target_row_number, title_col, package["title"])
        content_sheet.update_cell(target_row_number, script_col, package["script"])
        content_sheet.update_cell(target_row_number, description_col, package["description"])
        content_sheet.update_cell(
            target_row_number,
            scene_prompts_col,
            json.dumps(scene_payload, ensure_ascii=False),
        )
        content_sheet.update_cell(target_row_number, status_col, "GENERATED")
        content_sheet.update_cell(target_row_number, created_at_col, now)
        content_sheet.update_cell(target_row_number, image_status_col, "PENDING")
        content_sheet.update_cell(target_row_number, audio_status_col, "PENDING")
        content_sheet.update_cell(target_row_number, youtube_status_col, "")
        content_sheet.update_cell(target_row_number, youtube_video_id_col, "")

        log(logs_sheet, video_id, "GENERATE_STORY", f"Generated story with cleaned Arabic subtitles: {package['title']}")
        print(f"Generated story: {package['title']}")

    except Exception as exc:
        content_sheet.update_cell(target_row_number, status_col, "FAILED_STORY")
        log(logs_sheet, video_id, "GENERATE_STORY_FAILED", str(exc))
        raise


if __name__ == "__main__":
    main()
