# Tiny Brave Tails Auto

Cleaned GitHub Actions version. Use one pipeline: generate story → render video → upload to YouTube.

## Required GitHub Secrets

- `GOOGLE_SHEET_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `GEMINI_API_KEY`
- `YOUTUBE_CLIENT_ID`
- `YOUTUBE_CLIENT_SECRET`
- `YOUTUBE_REFRESH_TOKEN`

Optional:

- `GEMINI_MODEL`
- `EDGE_TTS_VOICE`
- `EDGE_TTS_LONG_VOICE`
- `EDGE_TTS_BEDTIME_VOICE`

## Correct order

1. Run **01 Setup Sheet Schema** once.
2. Run **02 Seed Ideas** if your sheet has no IDEA rows.
3. Run **03 Create And Upload Now**.

Do not use old upload-only workflows. A GitHub runner starts empty every run, so upload-only cannot see a video from a previous run unless it creates it again.

## Google Sheet statuses

- `IDEA` → ready for story generation
- `GENERATED` → story ready for video render
- `VIDEO_CREATED` → video ready for upload
- `UPLOADED` → finished

## Notes

The repo was cleaned from duplicate workflows, `__pycache__`, placeholder `New.py` files, and the broken legacy `tbt_scripts` pipeline.
