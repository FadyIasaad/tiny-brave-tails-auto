import json
import os
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from tbt_common import (
    find_column,
    get_all_values,
    get_cell,
    get_sheets_client,
    get_worksheet,
    log,
    open_spreadsheet,
    require_env,
    update_cell,
    run_with_retry,
)

CONTENT_SHEET_NAME = "Content"
LOGS_SHEET_NAME = "Logs"
OUTPUT_DIR = Path("output")

def load_youtube_credentials():
    # Attempt to load from YOUTUBE_TOKEN_JSON first (new consolidated secret)
    token_json = os.getenv("YOUTUBE_TOKEN_JSON", "").strip()
    if token_json:
        try:
            info = json.loads(token_json)
            creds = Credentials.from_authorized_user_info(info)
            if not creds.valid:
                if creds.expired and creds.refresh_token:
                    print("Refreshing YouTube access token...")
                    creds.refresh(Request())
            return creds
        except Exception as e:
            print(f"Error loading YOUTUBE_TOKEN_JSON: {e}")

    # Fallback to individual secrets if they exist
    refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN")
    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")

    if refresh_token and client_id and client_secret:
        return Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=["https://www.googleapis.com/auth/youtube.upload"]
        )

    raise RuntimeError("Missing YouTube credentials. Provide YOUTUBE_TOKEN_JSON or individual secrets.")

def get_youtube_service():
    credentials = load_youtube_credentials()
    return build("youtube", "v3", credentials=credentials)

def find_video_for_id(video_id):
    safe_id = str(video_id).strip()
    candidates = list(OUTPUT_DIR.glob(f"*{safe_id}*.mp4")) if safe_id else []
    if not candidates:
        candidates = list(OUTPUT_DIR.glob("*.mp4"))
    if not candidates:
        raise FileNotFoundError("No MP4 video found inside output folder.")
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0]

def upload_video_to_youtube(video_path, title, description):
    youtube = get_youtube_service()
    privacy_status = os.getenv("YOUTUBE_PRIVACY", "private")
    request_body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "categoryId": "1",
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": True,
        },
    }
    media = MediaFileUpload(str(video_path), resumable=True, chunksize=1024 * 1024)
    request = youtube.videos().insert(part="snippet,status", body=request_body, media_body=media)
    response = None
    while response is None:
        upload_status, response = run_with_retry("Uploading YouTube chunk", lambda: request.next_chunk(), max_attempts=5)
        if upload_status:
            print(f"Upload progress: {int(upload_status.progress() * 100)}%")
    return response["id"], privacy_status

def main():
    require_env("GOOGLE_SHEET_ID")
    require_env("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheets_client = get_sheets_client()
    spreadsheet = open_spreadsheet(sheets_client)
    content_sheet = get_worksheet(spreadsheet, CONTENT_SHEET_NAME)
    logs_sheet = None
    try: logs_sheet = get_worksheet(spreadsheet, LOGS_SHEET_NAME)
    except: pass

    values = get_all_values(content_sheet)
    if not values: return
    headers = values[0]
    id_col = find_column(headers, "id")
    title_col = find_column(headers, "title")
    description_col = find_column(headers, "description")
    status_col = find_column(headers, "status")
    youtube_status_col = find_column(headers, "youtube_status")
    youtube_video_id_col = find_column(headers, "youtube_video_id")
    video_url_col = find_column(headers, "video_url")

    target_row_number, target_row = None, None
    for index, row in enumerate(values[1:], start=2):
        if get_cell(row, status_col).upper() == "VIDEO_CREATED" and not get_cell(row, youtube_status_col).startswith("UPLOADED"):
            target_row_number, target_row = index, row
            break

    if target_row_number is None:
        print("No row waiting for upload.")
        return

    video_id = get_cell(target_row, id_col)
    title = get_cell(target_row, title_col)
    description = get_cell(target_row, description_col) or "A tiny brave story."

    try:
        video_path = find_video_for_id(video_id)
        yt_id, privacy = upload_video_to_youtube(video_path, title, description)
        update_cell(content_sheet, target_row_number, youtube_status_col, f"UPLOADED_{privacy.upper()}")
        update_cell(content_sheet, target_row_number, youtube_video_id_col, yt_id)
        update_cell(content_sheet, target_row_number, video_url_col, f"https://youtu.be/{yt_id}")
        update_cell(content_sheet, target_row_number, status_col, "UPLOADED")
        log(logs_sheet, video_id, "UPLOAD_YOUTUBE", f"Uploaded successfully: {yt_id}")
    except Exception as e:
        print(f"Upload failed: {e}")
        log(logs_sheet, video_id, "FAILED_UPLOAD", str(e))
        raise

if __name__ == "__main__":
    main()
