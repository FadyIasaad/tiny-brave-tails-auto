# Content Backlog Guide

Your sheet should not depend on only 3 starter rows. Use the GitHub button below whenever the Content sheet starts getting empty.

## Button
Actions → **00 Generate Content Backlog**

Recommended settings:
- `video_type`: `all`
- `count_per_type`: `20`

This keeps at least 20 IDEA rows for each type:
- short
- bedtime
- long_story
- toby_collection
- calming
- adventure

The create/upload buttons always pick the first row where:
- `status = IDEA`
- `video_type` matches the button

After the workflow uses a row, it changes the row status so the next button run moves to the next idea.

## Important
Do not manually delete the headers. If the sheet gets messy, run:
1. Setup Sheet Schema
2. 00 Generate Content Backlog
3. Your chosen Create/Upload button
