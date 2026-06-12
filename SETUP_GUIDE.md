# Setup Guide

## Secrets needed in GitHub

Add these in **Repository Settings → Secrets and variables → Actions → New repository secret**:

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

## Run order

1. Run **01 Setup Sheet Schema**.
2. Run **02 Seed Ideas**.
3. Run **03 Create And Upload Now**.

## Important

Use a Google OAuth **Web application** client for YouTube, because this repo refreshes the token in GitHub Actions.

The old `YOUTUBE_TOKEN_JSON` / `tbt_scripts` flow was removed because it was causing broken Actions.
