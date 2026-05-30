import argparse
import json
from pathlib import Path
from tbt_config import VIDEO_TYPES, METADATA_DIR
from .story_engine import generate_script
from .voice_engine import generate_voice
from .video_engine import render_video
from .youtube_uploader import upload_video, add_to_playlist

def run(video_type: str):
    if video_type not in VIDEO_TYPES:
        raise ValueError(f"Unknown video type: {video_type}. Available: {', '.join(VIDEO_TYPES)}")

    settings = VIDEO_TYPES[video_type]
    print(f"Generating: {video_type} -> {settings['category']}")

    story_data = generate_script(video_type, settings)
    audio_path = generate_voice(story_data["narration"], settings["voice"], video_type)
    video_path = render_video(story_data, audio_path, settings, video_type)

    video_id = upload_video(video_path, story_data)
    add_to_playlist(video_id, story_data["category"])

    METADATA_DIR.mkdir(parents=True, exist_ok=True)
    metadata_path = METADATA_DIR / f"{video_type}_last_run.json"
    metadata_path.write_text(json.dumps({
        "video_type": video_type,
        "video_id": video_id,
        "video_path": video_path,
        "title": story_data["title"],
        "category": story_data["category"],
    }, indent=2), encoding="utf-8")

    print("DONE")
    print(f"Video ID: {video_id}")
    print(f"Category: {story_data['category']}")
    print(f"Video: {video_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", required=True, choices=list(VIDEO_TYPES.keys()))
    args = parser.parse_args()
    run(args.type)
