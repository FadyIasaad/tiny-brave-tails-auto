# Tiny Brave Tails Auto Studio — Emotional Bedtime Upgrade

This update cleans the old mixed pipeline and makes the bedtime stories less flat.

## What changed

- Story generation now uses a 7-beat emotional arc instead of a short 6-scene summary.
- Every scene now carries `emotion`, `voice_style`, `pause_after`, and `camera_motion`.
- Video generation now uses Edge TTS first, with espeak-ng only as fallback.
- Each scene gets subtle camera motion: zoom, pan, or tiny handheld movement.
- Old `fetch_visuals.py` and `generate_images.py` no longer create conflicting 3-scene logic. They are wrappers around the new video pipeline.
- Upload now selects the MP4 that matches the row video id instead of blindly uploading the latest file.
- GitHub Actions now use Python 3.11 and upload the rendered output as an artifact for checking.

## Required Google Sheet columns

Your `Content` tab must include these headers:

`id, topic, animal, lesson, script, title, description, status, video_url, created_at, scene_prompts, image_status, audio_status, youtube_status, youtube_video_id`

Use `IDEA` in the `status` column for the next video you want to create.

## Secrets needed

- `GOOGLE_SHEET_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `GEMINI_API_KEY`
- `YOUTUBE_CLIENT_ID`
- `YOUTUBE_CLIENT_SECRET`
- `YOUTUBE_REFRESH_TOKEN`

## Best button to use

Use **Create And Upload Now**.

It will:

1. Generate emotional story.
2. Generate emotional video.
3. Verify MP4 exists.
4. Upload private to YouTube.
5. Save the YouTube URL back to the sheet.

## Important truth

This is still a free automation engine. It will be much better than the flat version, but it is not premium character-consistent AI animation. The next big jump is adding your own reusable character PNG assets.
