import os
import json
import re
from datetime import datetime, timezone

import gspread
import google.generativeai as genai
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


def get_sheets_client():
    service_account_info = json.loads(SERVICE_ACCOUNT_JSON)
    credentials = Credentials.from_service_account_info(
        service_account_info,
        scopes=SCOPES,
    )
    return gspread.authorize(credentials)


def clean_json_response(text):
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in Gemini response: {text}")

    return text[start:end + 1]


def generate_story_package(topic, animal, lesson):
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.5-flash")

    prompt = f"""
You are the lead writer for a YouTube Shorts channel called Tiny Brave Tails.

Channel:
Family-friendly emotional 2D storybook animal stories with life lessons.

Goal:
Create a short viral YouTube Shorts story package.

Style:
- Warm 2D cartoon storybook.
- Cute emotional animals.
- Simple English.
- Strong emotional hook in the first 2 seconds.
- Fast pacing.
- No horror.
- No gore.
- No explicit violence.
- No claim that the story is real.
- Appealing to adults and children.
- The story should feel like a tiny animated short.

Input:
Topic: {topic}
Animal: {animal}
Life lesson: {lesson}

Return valid JSON only.

Required JSON:
{{
  "title": "Short emotional YouTube title under 70 characters",
  "script": "Full English narration script, 35 to 55 seconds",
  "description": "Short YouTube description with hashtags",
  "hook": "The strongest first sentence of the story",
  "arabic_summary": "Short Arabic summary of the story",
  "character": {{
    "name": "Character name",
    "description": "Consistent character design in English. Include color, eyes, accessory, mood, and style."
  }},
  "scenes": [
    {{
      "scene_number": 1,
      "en_subtitle": "Short English subtitle for this scene",
      "ar_subtitle": "ترجمة عربية قصيرة لهذا المشهد",
      "image_prompt": "Prompt for a vertical 9:16 warm 2D cartoon storybook frame matching this scene"
    }}
  ]
}}

Scene rules:
- Exactly 6 scenes.
- Each English subtitle must be short and punchy.
- Each Arabic subtitle must be natural, short, and emotionally clear.
- The 6 scenes must follow: hook, problem, fear, brave action, emotional turn, lesson.
- Keep the same character design in every image prompt.
- Each image prompt must include: warm 2D cartoon storybook style, soft colors, expressive animal face, child-safe, vertical 9:16, no text.
"""

    response = model.generate_content(prompt)
    data = json.loads(clean_json_response(response.text))

    required = ["title", "script", "description", "hook", "arabic_summary", "character", "scenes"]
    for key in required:
        if key not in data:
            raise ValueError(f"Missing key from Gemini response: {key}")

    scenes = data["scenes"]
    if not isinstance(scenes, list) or len(scenes) != 6:
        raise ValueError(f"Expected exactly 6 scenes, got: {len(scenes) if isinstance(scenes, list) else 'invalid'}")

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

    package = generate_story_package(topic, animal, lesson)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    scene_payload = {
        "hook": package["hook"],
        "arabic_summary": package["arabic_summary"],
        "character": package["character"],
        "scenes": package["scenes"],
    }

    content_sheet.update_cell(target_row_number, title_col, package["title"])
    content_sheet.update_cell(target_row_number, script_col, package["script"])
    content_sheet.update_cell(target_row_number, description_col, package["description"])
    content_sheet.update_cell(target_row_number, scene_prompts_col, json.dumps(scene_payload, ensure_ascii=False))
    content_sheet.update_cell(target_row_number, status_col, "GENERATED")
    content_sheet.update_cell(target_row_number, created_at_col, now)
    content_sheet.update_cell(target_row_number, image_status_col, "PENDING")
    content_sheet.update_cell(target_row_number, audio_status_col, "PENDING")
    content_sheet.update_cell(target_row_number, youtube_status_col, "")
    content_sheet.update_cell(target_row_number, youtube_video_id_col, "")

    log(
        logs_sheet,
        video_id,
        "GENERATE_STORY",
        f"Generated 6-scene 2D storybook package: {package['title']}",
    )

    print(f"Generated story package: {package['title']}")


if __name__ == "__main__":
    main()
