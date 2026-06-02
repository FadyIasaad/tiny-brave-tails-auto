# GitHub Action Buttons

Use these buttons from **Actions**:

1. **01 Create Upload Short** — 45–75 second emotional short.
2. **02 Create Upload Bedtime Story** — long calm 30-minute story.
3. **03 Create Upload Long Emotional Story** — main 30-minute emotional story.
4. **04 Create Upload Toby Collection** — 45-minute collection around Toby.
5. **05 Create Upload Calming Story** — slow calming story, not kids content.
6. **06 Create Upload Adventure Story** — deeper adventure story, not kids content.
7. **Create And Upload Now** — manual selector if you want one workflow with a dropdown.
8. **Upload YouTube Only** — uploads an already-generated MP4 from `output/`.

Important: every workflow searches the Google Sheet for the first row with `status = IDEA` and matching `video_type`.

Allowed `video_type` values:

- `short`
- `bedtime`
- `long_story`
- `toby_collection`
- `calming`
- `adventure`

All default uploads are `private`, category `Entertainment`, and `made_for_kids = FALSE`.
