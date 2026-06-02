# Tiny Brave Tails - Long Emotional Story Upgrade

This version changes the project direction from short kids-style videos to long emotional animal stories for a general audience.

## What changed

- Toby the Turtle is now the main recurring character.
- Default content is **not made for kids**.
- YouTube category defaults to Entertainment (`24`).
- Long videos are supported through `video_type`, `target_minutes`, `main_character`, `audience`, and `made_for_kids` columns.
- Gemini now generates deep emotional long-form story packages instead of only 7-scene shorts.
- Scene prompts are protected: if Gemini forgets a field, the workflow fills it instead of crashing.
- Audio is slower, calmer, normalized, and less robotic.
- Video output goes to `output/videos/` and keeps 1080x1920 CRF 18 quality.
- Create-and-upload workflow timeout is extended for long videos.

## Required Content sheet headers

Use these headers in row 1:

```text
id, topic, animal, lesson, video_type, target_minutes, main_character, story_universe, audience, made_for_kids, script, title, description, status, video_url, created_at, scene_prompts, image_status, audio_status, youtube_status, youtube_video_id, video_file_path, error_message
```

## Recommended rows

Use `status = IDEA` for the next video to generate.

Recommended `video_type` values:

- `long_story` = 30 min default
- `toby_collection` = 45 min default
- `bedtime` = calm long story
- `calming` = slower reflective story
- `short` = old short format, only when needed

## Important YouTube setting

The upload code sends:

```json
"selfDeclaredMadeForKids": false
```

Do not write "for kids", "nursery", "children", or "cartoon for kids" in titles/descriptions unless you actually want YouTube to classify it that way.

## First run

1. Upload all files from this ZIP to GitHub.
2. Update your Google Sheet headers using the included Excel template or run:

```bash
python setup_sheet_schema.py
```

3. In GitHub Actions, run:

```text
Create And Upload Now
```

## Honest warning

30-60 minute videos take much longer to generate than Shorts. GitHub Actions can handle it, but image generation and TTS can timeout if external free services are slow. If that happens, lower `target_minutes` to 30 first, then scale to 45/60 after you confirm the pipeline is stable.
