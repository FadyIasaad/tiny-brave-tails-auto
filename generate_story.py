import json
import os
import re
from datetime import datetime, timezone

import google.generativeai as genai

from tbt_common import (
    get_sheets_client,
    open_spreadsheet,
    get_worksheet,
    get_all_records,
    get_all_values,
    update_cell,
    find_column,
    find_optional_column,
    log,
    require_env,
    run_with_retry,
)

CONTENT_SHEET_NAME = os.getenv("CONTENT_SHEET_NAME", "Content").strip()
LOGS_SHEET_NAME = os.getenv("LOGS_SHEET_NAME", "Logs").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

MODEL_CANDIDATES = [
    os.getenv("GEMINI_MODEL", "gemini-1.5-flash").strip(),
    "gemini-1.5-flash-lite",
    "gemini-2.0-flash-exp",
]
MODEL_CANDIDATES = list(dict.fromkeys([m for m in MODEL_CANDIDATES if m]))

REQUIRED_COLUMNS = [
    "id", "topic", "animal", "lesson", "script", "title", "description", "status",
    "video_url", "created_at", "scene_prompts", "image_status", "audio_status",
    "youtube_status", "youtube_video_id", "video_file_path", "error_message",
]

EMOTIONAL_BEATS = [
    "cold open hook with danger or loneliness",
    "show the small hero's wound or fear",
    "raise the problem and make it personal",
    "moment of doubt, almost giving up",
    "brave choice with emotional sacrifice",
    "warm rescue / connection / relief",
    "quiet bedtime lesson that lands softly",
]

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def ensure_headers(sheet):
    values = get_all_values(sheet)
    if not values:
        update_cell(sheet, 1, 1, REQUIRED_COLUMNS[0])
        for idx, header in enumerate(REQUIRED_COLUMNS, start=1):
            update_cell(sheet, 1, idx, header)
        return REQUIRED_COLUMNS
    headers = [str(h).strip() for h in values[0]]
    missing = [h for h in REQUIRED_COLUMNS if h not in headers]
    if missing:
        raise RuntimeError(
            "Content sheet is missing required columns: " + ", ".join(missing) +
            "\nYour first row must contain: " + ", ".join(REQUIRED_COLUMNS)
        )
    return headers

def find_first_idea(records):
    for row_number, record in enumerate(records, start=2):
        if str(record.get("status", "")).strip().upper() == "IDEA" and str(record.get("topic", "")).strip():
            return row_number, record
    return None, None

def clean_json_text(text):
    if not text:
        raise RuntimeError("Gemini returned empty text.")
    cleaned = text.strip()
    if "```" in cleaned:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(0)
        else:
            cleaned = re.sub(r"^```json\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"^```\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()

def parse_json_response(text):
    cleaned = clean_json_text(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Malformed JSON: {e}. Preview: {cleaned[:500]}")
        raise RuntimeError(f"Could not find valid JSON. Preview: {cleaned[:500]}")

def build_prompt(record):
    topic = str(record.get("topic", "")).strip()
    animal = str(record.get("animal", "")).strip()
    lesson = str(record.get("lesson", "")).strip()
    beats = "\n".join(f"{i+1}. {beat}" for i, beat in enumerate(EMOTIONAL_BEATS))
    return f"""
You write for a YouTube Shorts channel called Tiny Brave Tails.
Create an emotional bedtime story for a global audience.

Inputs:
Topic: {topic}
Animal: {animal}
Life lesson: {lesson}

Rules:
- English only.
- Mini story, not educational narration.
- The hero must want something, fear something, and make one brave choice.
- English narration target: 95 to 140 words.
- Exactly 7 scenes matching these emotional beats:
{beats}

Return valid JSON only:
{{
  "title": "YouTube title under 70 characters",
  "script": "Full voiceover script text",
  "description": "Short description with #shorts #animalstory #emotionalstory #lifelessons #tinybravetails",
  "scene_prompts": {{
    "character": {{
      "name": "short animal name",
      "animal_type": "{animal}",
      "description": "consistent 2D storybook animal design description"
    }},
    "scenes": [
      {{
        "scene_number": 1,
        "emotion": "one of: wonder, lonely, worried, afraid, brave, relieved, peaceful",
        "narration_en": "1-2 short sentences",
        "subtitle_en": "subtitle-safe English",
        "subtitle_ar": "Arabic translation of the narration",
        "image_prompt": "vertical 9:16 warm 2D cartoon storybook frame, same character, no text"
      }}
    ]
  }}
}}
""".strip()

def validate_story(data, record):
    if not isinstance(data, dict):
        raise RuntimeError("Gemini response is not a JSON object.")
    title = str(data.get("title", "")).strip()
    script = str(data.get("script", "")).strip()
    description = str(data.get("description", "")).strip()
    if not title or not script or not description:
        raise RuntimeError("Gemini response missing title, script, or description.")

    scene_payload = data.get("scene_prompts")
    if not isinstance(scene_payload, dict) or "scenes" not in scene_payload or len(scene_payload["scenes"]) < 3:
         raise RuntimeError("Missing or invalid scene_prompts in Gemini response.")

    return {
        "title": title[:95],
        "script": script,
        "description": description[:4900],
        "scene_prompts": json.dumps(scene_payload, ensure_ascii=False),
    }

def configure_gemini():
    if not GEMINI_API_KEY:
        raise RuntimeError("Missing GEMINI_API_KEY")
    genai.configure(api_key=GEMINI_API_KEY)

def generate_story(record):
    configure_gemini()
    prompt = build_prompt(record)
    errors = []
    for model_name in MODEL_CANDIDATES:
        print(f"Trying Gemini model: {model_name}")
        def one_complete_generation():
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            data = parse_json_response(response.text)
            return validate_story(data, record)
        try:
            story = run_with_retry(f"Generating story with {model_name}", one_complete_generation, max_attempts=3)
            return story, model_name
        except Exception as exc:
            errors.append(f"{model_name}: {exc}")
            continue
    raise RuntimeError("All models failed. " + " | ".join(errors))

def main():
    require_env("GOOGLE_SHEET_ID")
    require_env("GOOGLE_SERVICE_ACCOUNT_JSON")
    require_env("GEMINI_API_KEY")
    client = get_sheets_client()
    spreadsheet = open_spreadsheet(client)
    content_sheet = get_worksheet(spreadsheet, CONTENT_SHEET_NAME)
    logs_sheet = None
    try:
        logs_sheet = get_worksheet(spreadsheet, LOGS_SHEET_NAME)
    except: pass
    headers = ensure_headers(content_sheet)
    records = get_all_records(content_sheet)
    row_number, record = find_first_idea(records)
    if not record:
        print("No IDEA rows found.")
        return
    video_id = str(record.get("id", "")).strip()
    try:
        story, model_used = generate_story(record)
        col = {name: idx + 1 for idx, name in enumerate(headers)}
        updates = {
            "script": story["script"],
            "title": story["title"],
            "description": story["description"],
            "scene_prompts": story["scene_prompts"],
            "status": "GENERATED",
            "image_status": "PENDING",
            "created_at": now_iso(),
        }
        for name, value in updates.items():
            if name in col:
                update_cell(content_sheet, row_number, col[name], value)
        log(logs_sheet, video_id, "GENERATE_STORY", f"Generated using {model_used}")
    except Exception as exc:
        print(f"Failed: {exc}")
        log(logs_sheet, video_id, "FAILED_STORY", str(exc))
        raise

if __name__ == "__main__":
    main()
