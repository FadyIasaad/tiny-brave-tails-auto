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
        text = re.sub(r"^```(?:json)?", "", text.strip(), flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text.strip()).strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in Gemini response: {text}")

    return text[start:end + 1]


def generate_story_package(topic, animal, lesson):
    genai.configure(api_key=GEMINI_API_KEY)

    model = genai.GenerativeModel("gemini-2.5-flash")

    prompt = f"""
You are writing content for a YouTube Shorts channel called Tiny Brave Tails.

Channel concept:
Short emotional animal stories with simple life lessons for an international English-speaking audience.

Your job:
Create one short story package.

Strict rules:
- English only.
- Family-friendly.
- Emotional and heartwarming.
- No gore.
- No horror.
- No explicit violence.
- No claim that the story is real.
- Strong hook in the first sentence.
- Short, clear, easy English.
- The full script should be around 35 to 50 seconds when read aloud.
- End with one clear life lesson.
- Do not mention AI.
- Do not use markdown.
- Return valid JSON only.

Story inputs:
Topic: {topic}
Animal: {animal}
Life lesson: {lesson}

You must return exactly this JSON structure:
{{
  "title": "Short YouTube title under 70 characters",
  "script": "Full narration script",
  "description": "Short YouTube description with hashtags",
  "scenes": [
    {{
      "scene_number": 1,
      "text": "Short sentence or moment from the story",
      "image_prompt": "Highly visual prompt for an emotional storybook-style illustration matching the moment, vertical 9:16"
    }},
    {{
      "scene_number": 2,
      "text": "Short sentence or moment from the story",
      "image_prompt": "Highly visual prompt for an emotional storybook-style illustration matching the moment, vertical 9:16"
    }},
    {{
      "scene_number": 3,
      "text": "Short sentence or moment from the story",
      "image_prompt": "Highly visual prompt for an emotional storybook-style illustration matching the moment, vertical 9:16"
    }}
  ]
}}

Scene rules:
- Exactly 3 scenes.
- The scenes must follow the story in order: beginning, middle, ending.
- Each image prompt must visually match the story moment.
- Style of every image prompt: emotional storybook illustration, cinematic lighting, expressive animal emotions, child-safe, appealing to both kids and adults, vertical 9:16.
- Keep recurring character details consistent across the 3 image prompts.
"""

    response = model.generate_content(prompt)
    raw_text = response.text

    json_text = clean_json_response(raw_text)
    data = json.loads(json_text)

    title = str(data.get("title", "")).strip()
    script = str(data.get("script", "")).strip()
    description = str(data.get("description", "")).strip()
    scenes = data.get("scenes", [])

    if not title or not script or not description:
        raise ValueError(f"Gemini returned incomplete story data: {data}")

    if not isinstance(scenes, list) or len(scenes) != 3:
        raise ValueError(f"Gemini returned invalid scenes data: {data}")

    normalized_scenes = []
    for i, scene in enumerate(scenes, start=1):
        scene_number = scene.get("scene_number", i)
        text = str(scene.get("text", "")).strip()
        image_prompt = str(scene.get("image_prompt", "")).strip()

        if not text or not image_prompt:
            raise ValueError(f"Gemini returned incomplete scene at position {i}: {scene}")

        normalized_scenes.append(
            {
                "scene_number": int(scene_number),
                "text": text,
                "image_prompt": image_prompt,
            }
        )

    return title, script, description, normalized_scenes


def find_column(headers, name):
    if name not in headers:
        raise ValueError(f"Missing required column: {name}")
    return headers.index(name) + 1


def log(logs_sheet, video_id, action, message):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    logs_sheet.append_row(
        [now, video_id, action, message],
        value_input_option="USER_ENTERED",
    )


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

    for index, row in enumerate(all_values[1:], start=2):
        status = row[status_col - 1].strip() if len(row) >= status_col else ""

        if status == "IDEA":
            target_row_number = index
            target_row = row
            break

    if target_row_number is None:
        log(logs_sheet, "", "GENERATE_STORY", "No IDEA row found.")
        print("No IDEA row found.")
        return

    def get_cell(row, col):
        return row[col - 1].strip() if len(row) >= col else ""

    video_id = get_cell(target_row, id_col)
    topic = get_cell(target_row, topic_col)
    animal = get_cell(target_row, animal_col)
    lesson = get_cell(target_row, lesson_col)

    if not topic or not animal or not lesson:
        raise ValueError(f"Missing topic/animal/lesson in row {target_row_number}")

    title, script, description, scenes = generate_story_package(topic, animal, lesson)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    scene_prompts_json = json.dumps(scenes, ensure_ascii=False)

    content_sheet.update_cell(target_row_number, title_col, title)
    content_sheet.update_cell(target_row_number, script_col, script)
    content_sheet.update_cell(target_row_number, description_col, description)
    content_sheet.update_cell(target_row_number, scene_prompts_col, scene_prompts_json)
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
        f"Generated story + 3 scene prompts for row {target_row_number}: {title}",
    )

    print(f"Generated story + 3 scene prompts for row {target_row_number}: {title}")


if __name__ == "__main__":
    main()
