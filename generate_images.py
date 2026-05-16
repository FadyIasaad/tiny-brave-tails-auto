import os
import json
import base64
from datetime import datetime, timezone
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from google import genai
from google.genai import types


SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

CONTENT_SHEET_NAME = "Content"
LOGS_SHEET_NAME = "Logs"

OUTPUT_DIR = Path("output/images")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

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


def generate_one_image(client, prompt, output_path):
    final_prompt = f"""
Create a vertical 9:16 emotional storybook illustration.

Scene:
{prompt}

Style requirements:
- warm cinematic lighting
- expressive animal emotion
- family-friendly
- soft detailed storybook illustration
- no text in the image
- no watermark text
- no logos
- vertical composition for YouTube Shorts
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=final_prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
        ),
    )

    for part in response.candidates[0].content.parts:
        if part.inline_data and part.inline_data.data:
            image_bytes = part.inline_data.data
            output_path.write_bytes(image_bytes)
            return output_path

    raise ValueError("No image data returned from Gemini.")


def main():
    sheets_client = get_sheets_client()
    spreadsheet = sheets_client.open_by_key(SHEET_ID)

    content_sheet = spreadsheet.worksheet(CONTENT_SHEET_NAME)
    logs_sheet = spreadsheet.worksheet(LOGS_SHEET_NAME)

    all_values = content_sheet.get_all_values()
    headers = all_values[0]

    id_col = find_column(headers, "id")
    status_col = find_column(headers, "status")
    scene_prompts_col = find_column(headers, "scene_prompts")
    image_status_col = find_column(headers, "image_status")

    target_row_number = None
    target_row = None

    for index, row in enumerate(all_values[1:], start=2):
        status = get_cell(row, status_col)
        image_status = get_cell(row, image_status_col)

        if status == "GENERATED" and image_status == "PENDING":
            target_row_number = index
            target_row = row
            break

    if target_row_number is None:
        log(logs_sheet, "", "GENERATE_IMAGES", "No GENERATED row with PENDING image_status found.")
        print("No row ready for image generation.")
        return

    video_id = get_cell(target_row, id_col)
    scene_prompts_raw = get_cell(target_row, scene_prompts_col)

    if not scene_prompts_raw:
        raise ValueError(f"Missing scene_prompts in row {target_row_number}")

    scenes = json.loads(scene_prompts_raw)

    if not isinstance(scenes, list) or len(scenes) != 3:
        raise ValueError(f"scene_prompts must contain exactly 3 scenes. Got: {scene_prompts_raw}")

    client = genai.Client(api_key=GEMINI_API_KEY)

    video_image_dir = OUTPUT_DIR / str(video_id)
    video_image_dir.mkdir(parents=True, exist_ok=True)

    created_paths = []

    for i, scene in enumerate(scenes, start=1):
        prompt = scene.get("image_prompt", "")
        if not prompt:
            raise ValueError(f"Missing image_prompt for scene {i}")

        output_path = video_image_dir / f"scene_{i}.png"
        generate_one_image(client, prompt, output_path)
        created_paths.append(str(output_path))

    content_sheet.update_cell(target_row_number, image_status_col, "CREATED")

    log(
        logs_sheet,
        video_id,
        "GENERATE_IMAGES",
        f"Generated 3 images for row {target_row_number}: {created_paths}",
    )

    print(f"Generated images: {created_paths}")


if __name__ == "__main__":
    main()
