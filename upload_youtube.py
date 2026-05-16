import os
import json
from datetime import datetime, timezone
from pathlib import Path

import gspread
from google.oauth2.credentials import Credentials
from google.oauth2.service_account import Credentials as ServiceCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

YOUTUBE_CLIENT_ID = os.environ["YOUTUBE_CLIENT_ID"]
YOUTUBE_CLIENT_SECRET = os.environ["YOUTUBE_CLIENT_SECRET"]
YOUTUBE_REFRESH_TOKEN = os.environ["YOUTUBE_REFRESH_TOKEN"]

CONTENT_SHEET_NAME = "Content"
LOGS_SHEET_NAME = "Logs"

OUTPUT_DIR = Path("output")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_sheets_client():
    service_account_info = json.loads(SERVICE_ACCOUNT_JSON)

    credentials = ServiceCredentials.from_service_account_info(
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


def get_youtube_service():
    creds = Credentials(
        None,
        refresh_token=YOUTUBE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=YOUTUBE_CLIENT_ID,
        client_secret=YOUTUBE_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )

    return build("youtube", "v3", credentials=creds)


def upload_video(video_path, title, description):
    youtube = get_youtube_service()

    request_body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "categoryId": "1",
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        resumable=True,
        chunksize=1024 * 1024,
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=request_body,
        media_body=media,
    )

    response = None

    while response is None:
        status, response = request.next_chunk()

        if status:
            progress = int(status.progress() * 100)
            print(f"Upload progress: {progress}%")

    return response["id"]


def main():
    client = get_sheets_client()

    spreadsheet = client.open_by_key(SHEET_ID)

    content_sheet = spreadsheet.worksheet(CONTENT_SHEET_NAME)
    logs_sheet = spreadsheet.worksheet(LOGS_SHEET_NAME)

    values = content_sheet.get_all_values()
    headers = values[0]

    id_col = find_column(headers, "id")
    title_col = find_column(headers, "title")
    description_col = find_column(headers, "description")
    status_col = find_column(headers, "status")
    youtube_status_col = find_column(headers, "youtube_status")
    youtube_video_id_col = find_column(headers, "youtube_video_id")
    video_url_col = find_column(headers, "video_url")

    target_row_number = None
    target_row = None

    for index, row in enumerate(values[1:], start=2):
        if get_cell(row, status_col) == "VIDEO_CREATED":
            target_row_number = index
            target_row = row
            break

    if target_row_number is None:
        print("No VIDEO_CREATED row found.")
        return

    video_id = get_cell(target_row, id_col)
    title = get_cell(target_row, title_col)
    description = get_cell(target_row, description_col)

    video_path = OUTPUT_DIR / f"tiny_brave_tails_{video_id}.mp4"

    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    youtube_video_id = upload_video(
        video_path=video_path,
        title=title,
        description=description,
    )

    youtube_url = f"https://youtu.be/{youtube_video_id}"

    content_sheet.update_cell(
        target_row_number,
        youtube_status_col,
        "UPLOADED_PRIVATE",
    )

    content_sheet.update_cell(
        target_row_number,
        youtube_video_id_col,
        youtube_video_id,
    )

    content_sheet.update_cell(
        target_row_number,
        video_url_col,
        youtube_url,
    )

    content_sheet.update_cell(
        target_row_number,
        status_col,
        "UPLOADED",
    )

    log(
        logs_sheet,
        video_id,
        "UPLOAD_YOUTUBE",
        f"Uploaded private video: {youtube_url}",
    )

    print(f"Uploaded successfully: {youtube_url}")


if __name__ == "__main__":
    main()
