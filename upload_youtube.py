import os
import json
import glob

import gspread
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google.oauth2.credentials import Credentials as YouTubeCredentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
YOUTUBE_TOKEN_JSON = os.getenv("YOUTUBE_TOKEN_JSON", "").strip()

CONTENT_WORKSHEET_NAME = os.getenv("CONTENT_WORKSHEET_NAME", "Content").strip()
LOGS_WORKSHEET_NAME = os.getenv("LOGS_WORKSHEET_NAME", "Logs").strip()

TARGET_STATUS = os.getenv("UPLOAD_TARGET_STATUS", "VIDEO_READY").strip()
UPLOADED_STATUS = os.getenv("UPLOADED_STATUS", "UPLOADED").strip()
FAILED_STATUS = os.getenv("FAILED_UPLOAD_STATUS", "FAILED_UPLOAD").strip()

PRIVACY_STATUS = os.getenv("YOUTUBE_PRIVACY_STATUS", "private").strip()
YOUTUBE_CATEGORY_ID = os.getenv("YOUTUBE_CATEGORY_ID", "15").strip()


def require_env(name, value):
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")


def run_with_retry(action_name, func, max_attempts=5):
    import time
    import random

    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            print(f"{action_name}... attempt {attempt}/{max_attempts}")
            return func()
        except Exception as e:
            last_error = e
            text = str(e).lower()

            retryable = any(
                x in text
                for x in [
                    "500",
                    "502",
                    "503",
                    "504",
                    "429",
                    "timeout",
                    "temporarily",
                    "service unavailable",
                    "rate limit",
                    "connection",
                ]
            )

            if not retryable:
                print(f"Non-retryable error during {action_name}: {e}")
                raise

            wait = min(60, (2 ** attempt) + random.uniform(0, 3))
            print(f"Temporary error during {action_name}: {e}")
            print(f"Waiting {wait:.1f} seconds...")
            time.sleep(wait)

    raise RuntimeError(f"{action_name} failed after retries. Last error: {last_error}")


def get_gspread_client():
    require_env("GOOGLE_SERVICE_ACCOUNT_JSON", GOOGLE_SERVICE_ACCOUNT_JSON)

    service_account_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = ServiceAccountCredentials.from_service_account_info(
        service_account_info,
        scopes=scopes,
    )

    return gspread.authorize(creds)


def get_worksheet(spreadsheet, name):
    return run_with_retry(
        f"Opening worksheet '{name}'",
        lambda: spreadsheet.worksheet(name),
        max_attempts=4,
    )


def get_all_values(ws):
    return run_with_retry(
        "Reading worksheet values",
        lambda: ws.get_all_values(),
        max_attempts=6,
    )


def header_map(headers):
    return {str(h).strip(): i for i, h in enumerate(headers)}


def find_first_row_by_status(values, headers, status):
    status_col = headers.get("status")

    if status_col is None:
        raise RuntimeError("Missing required column: status")

    for idx, row in enumerate(values[1:], start=2):
        while len(row) < len(values[0]):
            row.append("")

        if str(row[status_col]).strip().upper() == status.upper():
            return idx, row

    return None, None


def update_cell(ws, row, col_index_1_based, value):
    return run_with_retry(
        f"Updating cell R{row}C{col_index_1_based}",
        lambda: ws.update_cell(row, col_index_1_based, value),
        max_attempts=6,
    )


def append_log(ws, values):
    return run_with_retry(
        "Appending log row",
        lambda: ws.append_row(values, value_input_option="USER_ENTERED"),
        max_attempts=6,
    )


def load_youtube_credentials():
    require_env("YOUTUBE_TOKEN_JSON", YOUTUBE_TOKEN_JSON)

    try:
        token_data = json.loads(YOUTUBE_TOKEN_JSON)
    except json.JSONDecodeError as e:
        raise RuntimeError("YOUTUBE_TOKEN_JSON is not valid JSON.") from e

    required = [
        "token",
        "refresh_token",
        "token_uri",
        "client_id",
        "client_secret",
        "scopes",
    ]

    missing = [key for key in required if not token_data.get(key)]

    if missing:
        raise RuntimeError(
            "YOUTUBE_TOKEN_JSON is missing required fields: " + ", ".join(missing)
        )

    creds = YouTubeCredentials(
        token=token_data["token"],
        refresh_token=token_data["refresh_token"],
        token_uri=token_data["token_uri"],
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        scopes=token_data["scopes"],
    )

    if not creds.valid:
        print("Refreshing YouTube credentials...")
        creds.refresh(Request())

    return creds


def get_youtube_service():
    creds = load_youtube_credentials()
    return build("youtube", "v3", credentials=creds)


def find_latest_mp4():
    files = glob.glob("output/*.mp4")

    if not files:
        raise FileNotFoundError("No MP4 video found inside output folder.")

    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]


def resolve_video_path(video_id, row, headers):
    video_file_path_col = headers.get("video_file_path")

    if video_file_path_col is not None:
        path_from_sheet = str(row[video_file_path_col]).strip()

        if path_from_sheet:
            print(f"Using video file from sheet: {path_from_sheet}")

            if os.path.exists(path_from_sheet):
                return path_from_sheet

            print(f"Sheet video path does not exist in this runner: {path_from_sheet}")

    expected_path = f"output/tiny_brave_tails_{video_id}.mp4"

    if os.path.exists(expected_path):
        print(f"Using expected video path: {expected_path}")
        return expected_path

    latest = find_latest_mp4()
    print(f"Using latest MP4 found: {latest}")
    return latest


def build_description(description):
    description = str(description or "").strip()

    if "#shorts" not in description.lower():
        description += "\n\n#shorts #animalstory #emotionalstory #lifelessons #tinybravetails"

    return description.strip()


def upload_video_to_youtube(video_path, title, description):
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file does not exist: {video_path}")

    youtube = get_youtube_service()

    request_body = {
        "snippet": {
            "title": str(title or "Tiny Brave Tails").strip()[:100],
            "description": build_description(description),
            "categoryId": YOUTUBE_CATEGORY_ID,
            "tags": [
                "shorts",
                "animal story",
                "emotional story",
                "life lessons",
                "tiny brave tails",
            ],
        },
        "status": {
            "privacyStatus": PRIVACY_STATUS,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        chunksize=-1,
        resumable=True,
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=request_body,
        media_body=media,
    )

    response = None

    while response is None:
        upload_status, response = run_with_retry(
            "Uploading YouTube chunk",
            lambda: request.next_chunk(),
            max_attempts=5,
        )

        if upload_status:
            print(f"Upload progress: {int(upload_status.progress() * 100)}%")

    youtube_video_id = response.get("id")

    if not youtube_video_id:
        raise RuntimeError(f"YouTube response missing video ID: {response}")

    print(f"YouTube upload complete. Video ID: {youtube_video_id}")

    return youtube_video_id, PRIVACY_STATUS


def main():
    require_env("GOOGLE_SHEET_ID", GOOGLE_SHEET_ID)
    require_env("GOOGLE_SERVICE_ACCOUNT_JSON", GOOGLE_SERVICE_ACCOUNT_JSON)
    require_env("YOUTUBE_TOKEN_JSON", YOUTUBE_TOKEN_JSON)

    client = get_gspread_client()

    spreadsheet = run_with_retry(
        "Opening Google Spreadsheet",
        lambda: client.open_by_key(GOOGLE_SHEET_ID),
        max_attempts=6,
    )

    content_ws = get_worksheet(spreadsheet, CONTENT_WORKSHEET_NAME)
    logs_ws = get_worksheet(spreadsheet, LOGS_WORKSHEET_NAME)

    values = get_all_values(content_ws)

    if len(values) < 2:
        print("No data rows found.")
        return

    headers = header_map(values[0])

    required_columns = ["id", "title", "description", "status"]

    missing = [col for col in required_columns if col not in headers]

    if missing:
        raise RuntimeError("Missing required columns: " + ", ".join(missing))

    row_number, row = find_first_row_by_status(values, headers, TARGET_STATUS)

    if not row:
        print(f"No row with status {TARGET_STATUS}. Nothing to upload.")
        return

    id_col = headers["id"]
    title_col = headers["title"]
    description_col = headers["description"]
    status_col = headers["status"]

    video_url_col = headers.get("video_url")
    youtube_status_col = headers.get("youtube_status")
    youtube_video_id_col = headers.get("youtube_video_id")
    error_message_col = headers.get("error_message")

    video_id = str(row[id_col]).strip()
    title = str(row[title_col]).strip()
    description = str(row[description_col]).strip()

    try:
        video_path = resolve_video_path(video_id, row, headers)

        youtube_video_id, privacy_status = upload_video_to_youtube(
            video_path,
            title,
            description,
        )

        youtube_url = f"https://www.youtube.com/watch?v={youtube_video_id}"

        update_cell(content_ws, row_number, status_col + 1, UPLOADED_STATUS)

        if video_url_col is not None:
            update_cell(content_ws, row_number, video_url_col + 1, youtube_url)

        if youtube_status_col is not None:
            update_cell(content_ws, row_number, youtube_status_col + 1, privacy_status)

        if youtube_video_id_col is not None:
            update_cell(content_ws, row_number, youtube_video_id_col + 1, youtube_video_id)

        if error_message_col is not None:
            update_cell(content_ws, row_number, error_message_col + 1, "")

        append_log(logs_ws, ["UPLOAD_SUCCESS", video_id, youtube_url])

        print(f"Upload successful: {youtube_url}")

    except Exception as e:
        update_cell(content_ws, row_number, status_col + 1, FAILED_STATUS)

        if error_message_col is not None:
            update_cell(content_ws, row_number, error_message_col + 1, str(e)[:1000])

        append_log(logs_ws, ["UPLOAD_FAILED", video_id, str(e)[:1000]])

        raise


if __name__ == "__main__":
    main()
