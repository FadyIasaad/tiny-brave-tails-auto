import json
import os
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from tbt_common import (
    find_column,
    find_optional_column,
    get_all_values,
    get_cell,
    get_logs_worksheet,
    get_sheets_client,
    get_worksheet,
    log,
    open_spreadsheet,
    require_env,
    update_cell,
    update_optional,
)

CONTENT_SHEET_NAME = "Content"
OUTPUT_DIR = Path("output")

# Keep this scope because your current refresh token already works for uploading.
# Do not change it to youtube.force-ssl unless you intentionally generate a new refresh token.
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def is_permission_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return isinstance(exc, HttpError) and (
        "insufficient" in text
        or "insufficient permission" in text
        or "insufficient authentication scopes" in text
        or "insufficientpermissions" in text
        or "forbidden" in text
    )


def get_youtube_service():
    credentials = Credentials(
        token=None,
        refresh_token=require_env("YOUTUBE_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=require_env("YOUTUBE_CLIENT_ID"),
        client_secret=require_env("YOUTUBE_CLIENT_SECRET"),
        scopes=YOUTUBE_SCOPES,
    )
    credentials.refresh(Request())
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def normalize_type(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def find_video_for_id(video_id: str) -> Path:
    safe_id = str(video_id or "").strip()
    if not OUTPUT_DIR.exists():
        raise FileNotFoundError("output/ folder does not exist. Create the video before uploading.")

    all_mp4 = [p for p in OUTPUT_DIR.rglob("*.mp4") if p.is_file() and p.stat().st_size > 1024]
    if safe_id:
        matched = [p for p in all_mp4 if safe_id in p.stem or safe_id in p.name]
    else:
        matched = []

    candidates = matched or all_mp4
    if not candidates:
        raise FileNotFoundError("No valid MP4 video found anywhere inside output/.")

    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    chosen = candidates[0]
    print(f"Using video file: {chosen} ({chosen.stat().st_size / 1024 / 1024:.2f} MB)")
    return chosen


def load_playlist_id(category: Optional[str] = None) -> Optional[str]:
    explicit = os.getenv("YOUTUBE_PLAYLIST_ID", "").strip()
    if explicit:
        return explicit

    map_path = Path("playlist_map.json")
    if not category or not map_path.exists():
        return None

    try:
        playlist_map = json.loads(map_path.read_text(encoding="utf-8"))
        playlist_id = str(playlist_map.get(category, "")).strip()
        if playlist_id and not playlist_id.startswith("PASTE_"):
            return playlist_id
    except Exception as exc:
        print(f"Playlist map ignored: {exc}")

    return None


def add_to_playlist_if_configured(youtube, youtube_video_id: str, category: Optional[str] = None) -> None:
    playlist_id = load_playlist_id(category)
    if not playlist_id:
        print("No playlist configured. Skipping playlist insert.")
        return

    try:
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": youtube_video_id},
                }
            },
        ).execute()
        print(f"Added to playlist: {playlist_id}")
    except Exception as exc:
        # Upload already succeeded. Playlist failure must not fail the Action.
        if is_permission_error(exc):
            print("Playlist insert skipped: current token can upload but cannot manage playlists.")
            return
        print(f"Playlist insert skipped after upload because it failed: {exc}")


def upload_video_to_youtube(video_path: Path, title: str, description: str, category: Optional[str] = None) -> str:
    youtube = get_youtube_service()

    privacy = os.getenv("YOUTUBE_PRIVACY", "private").strip().lower()
    if privacy not in {"private", "unlisted", "public"}:
        privacy = "private"

    request_body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "categoryId": os.getenv("YOUTUBE_CATEGORY_ID", "24"),
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True, chunksize=8 * 1024 * 1024)
    request = youtube.videos().insert(part="snippet,status", body=request_body, media_body=media)

    response = None
    while response is None:
        upload_status, response = request.next_chunk()
        if upload_status:
            print(f"Upload progress: {int(upload_status.progress() * 100)}%")

    youtube_video_id = response.get("id")
    if not youtube_video_id:
        raise RuntimeError(f"YouTube upload did not return a video id: {response}")

    print(f"YouTube upload returned video id: {youtube_video_id}")

    # Critical fix:
    # Do NOT call videos.list here. Your token has youtube.upload scope only.
    # The upload can succeed, then videos.list fails with 403 insufficient scopes.
    add_to_playlist_if_configured(youtube, youtube_video_id, category)
    return youtube_video_id


def main():
    sheets_client = get_sheets_client()
    spreadsheet = open_spreadsheet(sheets_client)
    content_sheet = get_worksheet(spreadsheet, CONTENT_SHEET_NAME)
    logs_sheet = get_logs_worksheet(spreadsheet)

    values = get_all_values(content_sheet)
    if not values:
        raise ValueError("Content sheet is empty.")

    headers = values[0]
    id_col = find_column(headers, "id")
    title_col = find_column(headers, "title")
    description_col = find_column(headers, "description")
    status_col = find_column(headers, "status")
    youtube_status_col = find_column(headers, "youtube_status")
    youtube_video_id_col = find_column(headers, "youtube_video_id")
    video_url_col = find_column(headers, "video_url")
    video_file_path_col = find_optional_column(headers, "video_file_path")
    error_message_col = find_optional_column(headers, "error_message")
    video_type_col = find_optional_column(headers, "video_type")

    requested_video_type = normalize_type(os.getenv("TBT_VIDEO_TYPE", ""))

    target_row_number = None
    target_row = None

    for index, row in enumerate(values[1:], start=2):
        status = get_cell(row, status_col).strip().upper()
        youtube_status = get_cell(row, youtube_status_col).strip().upper()

        if status != "VIDEO_CREATED":
            continue
        if youtube_status in {"UPLOADED", "UPLOADED_PRIVATE"}:
            continue

        row_type = normalize_type(get_cell(row, video_type_col)) if video_type_col else ""
        if requested_video_type and row_type and row_type != requested_video_type:
            continue

        target_row_number = index
        target_row = row
        break

    if target_row_number is None or target_row is None:
        log(logs_sheet, "", "UPLOAD_YOUTUBE", "No VIDEO_CREATED row waiting for upload.")
        print("No VIDEO_CREATED row waiting for upload.")
        return

    video_id = get_cell(target_row, id_col)
    title = get_cell(target_row, title_col)
    description = get_cell(target_row, description_col) or (
        "A long emotional animal story for a general audience. Not made for kids.\n\n"
        "#animalstory #emotionalstory #bedtimestory #tinybravetails"
    )
    category = get_cell(target_row, video_type_col) if video_type_col else None

    if not title:
        raise ValueError(f"Missing title in row {target_row_number}")

    try:
        video_path = find_video_for_id(video_id)
        update_optional(content_sheet, target_row_number, video_file_path_col, str(video_path))

        youtube_video_id = upload_video_to_youtube(video_path, title, description, category)
        youtube_url = f"https://youtu.be/{youtube_video_id}"

        update_cell(content_sheet, target_row_number, youtube_status_col, "UPLOADED_PRIVATE")
        update_cell(content_sheet, target_row_number, youtube_video_id_col, youtube_video_id)
        update_cell(content_sheet, target_row_number, video_url_col, youtube_url)
        update_cell(content_sheet, target_row_number, status_col, "UPLOADED")
        update_optional(content_sheet, target_row_number, error_message_col, "")
        log(logs_sheet, video_id, "UPLOAD_YOUTUBE", f"Uploaded private video: {youtube_url}")
        print(f"Uploaded successfully: {youtube_url}")

    except Exception as exc:
        update_cell(content_sheet, target_row_number, youtube_status_col, "UPLOAD_ERROR")
        update_optional(content_sheet, target_row_number, error_message_col, str(exc)[:1500])
        log(logs_sheet, video_id, "UPLOAD_YOUTUBE_ERROR", str(exc))
        raise


if __name__ == "__main__":
    main()
