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


def generate_story(topic, animal, lesson):
    genai.configure(api_key=GEMINI_API_KEY)

    model = genai.GenerativeModel("gemini-1.5-flash")

    prompt = f"""
You are writing for a YouTube Shorts channel called Tiny Brave Tails.

Channel concept:
Short emotional animal stories with simple life lessons.

Rules:
- English only.
- Family-friendly.
- No violence.
- No horror.
- No claim that the story is true.
- Simple emotional language.
- Strong hook in the first sentence.
- 35 to 55 seconds when read aloud.
- End with one clear life lesson.
- Do not mention AI.
- Do not use markdown.
- Return valid JSON only.

Topic: {topic}
Animal: {animal}
Life lesson: {lesson}

Return exactly this JSON structure:
{{
  "title": "Short YouTube title under 70 characters",
  "script": "Full narration script",
  "description": "Short YouTube description with hashtags"
}}
"""

    response = model.generate_content(prompt)
    raw_text = response.text

    json_text = clean_json_response(raw_text)
    data = json.loads(json_text)

    title = str(data.get("title", "")).strip()
    script = str(data.get("script", "")).strip()
    description = str(data.get("description", "")).strip()

    if not title or not script or not description:
        raise ValueError(f"Gemini returned incomplete data: {data}")

    return title, script, description


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

    title, script, description = generate_story(topic, animal, lesson)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    content_sheet.update_cell(target_row_number, title_col, title)
    content_sheet.update_cell(target_row_number, script_col, script)
    content_sheet.update_cell(target_row_number, description_col, description)
    content_sheet.update_cell(target_row_number, status_col, "GENERATED")
    content_sheet.update_cell(target_row_number, created_at_col, now)

    log(
        logs_sheet,
        video_id,
        "GENERATE_STORY",
        f"Generated story for row {target_row_number}: {title}",
    )

    print(f"Generated story for row {target_row_number}: {title}")


if __name__ == "__main__":
    main()
