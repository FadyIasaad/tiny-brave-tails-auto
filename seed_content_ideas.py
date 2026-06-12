import csv
import json
import os
from pathlib import Path

from tbt_common import get_sheets_client, open_spreadsheet, get_worksheet, get_all_values, run_with_retry

CONTENT_SHEET_NAME = "Content"
IDEAS_FILE = Path(os.getenv("TBT_IDEAS_FILE", "content_ideas_by_type.csv"))

REQUIRED_HEADERS = [
    "id", "topic", "animal", "lesson", "video_type", "target_minutes", "main_character", "story_universe", "audience", "made_for_kids",
    "script", "title", "description", "status", "video_url", "created_at", "scene_prompts", "image_status", "audio_status",
    "youtube_status", "youtube_video_id", "video_file_path", "error_message"
]

VALID_TYPES = {"short", "bedtime", "long_story", "toby_collection", "calming", "adventure"}


def ensure_headers(sheet):
    values = get_all_values(sheet)
    if not values:
        run_with_retry("Writing Content headers", lambda: sheet.update("A1:W1", [REQUIRED_HEADERS], value_input_option="USER_ENTERED"))
        return REQUIRED_HEADERS, []
    headers = values[0]
    changed = False
    for h in REQUIRED_HEADERS:
        if h not in headers:
            headers.append(h)
            changed = True
    if changed:
        run_with_retry("Updating Content headers", lambda: sheet.update("A1:W1", [headers], value_input_option="USER_ENTERED"))
    return headers, values[1:]


def load_ideas():
    if not IDEAS_FILE.exists():
        raise FileNotFoundError(f"Ideas file not found: {IDEAS_FILE}")
    with IDEAS_FILE.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main():
    requested_type = os.getenv("TBT_VIDEO_TYPE", "all").strip().lower() or "all"
    count_per_type = int(os.getenv("TBT_COUNT_PER_TYPE", "20"))
    if requested_type != "all" and requested_type not in VALID_TYPES:
        raise ValueError(f"Invalid TBT_VIDEO_TYPE: {requested_type}. Use all or one of {sorted(VALID_TYPES)}")

    client = get_sheets_client()
    spreadsheet = open_spreadsheet(client)
    sheet = get_worksheet(spreadsheet, CONTENT_SHEET_NAME)
    headers, data_rows = ensure_headers(sheet)

    id_col = headers.index("id") if "id" in headers else 0
    existing_ids = {row[id_col].strip() for row in data_rows if len(row) > id_col and row[id_col].strip()}
    existing_idea_counts = {t: 0 for t in VALID_TYPES}
    status_col = headers.index("status") if "status" in headers else None
    type_col = headers.index("video_type") if "video_type" in headers else None
    if status_col is not None and type_col is not None:
        for row in data_rows:
            status = row[status_col].strip().upper() if len(row) > status_col else ""
            vtype = row[type_col].strip().lower() if len(row) > type_col else ""
            if status == "IDEA" and vtype in existing_idea_counts:
                existing_idea_counts[vtype] += 1

    rows_to_append = []
    added_counts = {t: 0 for t in VALID_TYPES}
    for item in load_ideas():
        vtype = (item.get("video_type") or "long_story").strip().lower()
        if vtype not in VALID_TYPES:
            continue
        if requested_type != "all" and vtype != requested_type:
            continue
        if existing_idea_counts[vtype] + added_counts[vtype] >= count_per_type:
            continue
        idea_id = (item.get("id") or "").strip()
        if not idea_id or idea_id in existing_ids:
            continue
        row = [""] * len(headers)
        for key, value in item.items():
            if key in headers:
                row[headers.index(key)] = str(value).strip()
        if "status" in headers and not row[headers.index("status")]:
            row[headers.index("status")] = "IDEA"
        if "made_for_kids" in headers and not row[headers.index("made_for_kids")]:
            row[headers.index("made_for_kids")] = "FALSE"
        rows_to_append.append(row)
        existing_ids.add(idea_id)
        added_counts[vtype] += 1

    if not rows_to_append:
        print("No new ideas needed. Existing IDEA backlog already meets the requested count.")
        print("Existing IDEA counts:", existing_idea_counts)
        return

    run_with_retry("Appending balanced content ideas", lambda: sheet.append_rows(rows_to_append, value_input_option="USER_ENTERED"))
    print(f"Appended {len(rows_to_append)} IDEA rows.")
    print("Added by type:", {k: v for k, v in sorted(added_counts.items()) if v})


if __name__ == "__main__":
    main()
