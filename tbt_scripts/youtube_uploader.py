import os
import json
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from config import DEFAULT_PRIVACY_STATUS

def _get_youtube_client():
    token_json = os.environ.get("YOUTUBE_TOKEN_JSON", "").strip()
    if not token_json:
        print("YOUTUBE_TOKEN_JSON not found.")
        return None

    try:
        info = json.loads(token_json)
        creds = Credentials.from_authorized_user_info(info)

        if not creds.valid:
            if creds.expired and creds.refresh_token:
                print("Refreshing YouTube access token...")
                creds.refresh(Request())

        youtube = build("youtube", "v3", credentials=creds)

        # Get channel info to let the user know where it's being uploaded
        channels_response = youtube.channels().list(part="snippet", mine=True).execute()
        if channels_response.get("items"):
            channel_title = channels_response["items"][0]["snippet"]["title"]
            print(f"Authenticated as YouTube channel: {channel_title}")

        return youtube
    except Exception as e:
        print(f"Error authenticating with YouTube: {e}")
        print("Dry-run mode: video was created but not uploaded.")
        return None

def upload_video(video_path, story_data):
    youtube = _get_youtube_client()
    if youtube is None:
        raise RuntimeError("YouTube authentication failed or YOUTUBE_TOKEN_JSON is missing. Cannot upload video.")

    body = {
        "snippet": {
            "title": story_data["title"][:100],
            "description": story_data["description"],
            "tags": story_data["tags"],
            "categoryId": "1",
        },
        "status": {
            "privacyStatus": DEFAULT_PRIVACY_STATUS,
            "selfDeclaredMadeForKids": True,
        },
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Upload progress: {int(status.progress() * 100)}%")

    return response["id"]

def add_to_playlist(video_id, category):
    if video_id == "DRY_RUN_VIDEO_ID":
        print(f"Dry-run playlist routing: {category}")
        return

    youtube = _get_youtube_client()
    if youtube is None:
        return

    playlist_map = json.loads(Path("playlist_map.json").read_text(encoding="utf-8"))
    playlist_id = playlist_map.get(category)

    if not playlist_id or playlist_id.startswith("PASTE_"):
        print(f"No playlist ID configured for {category}. Skipping playlist insert.")
        return

    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {
                    "kind": "youtube#video",
                    "videoId": video_id,
                },
            }
        },
    ).execute()
