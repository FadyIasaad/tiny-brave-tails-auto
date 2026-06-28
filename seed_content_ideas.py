import csv
import os
from pathlib import Path

from nd_common import get_sheets_client, open_spreadsheet, get_worksheet, get_all_values, run_with_retry

CONTENT_SHEET_NAME = "Content"
IDEAS_FILE = Path(os.getenv("TBT_IDEAS_FILE", "content_ideas_by_type.csv"))

REQUIRED_HEADERS = [
    "id", "topic", "characters", "theme", "video_type", "target_minutes", "narrator_pov", "setting", "audience", "made_for_kids",
    "script", "title", "description", "status", "video_url", "created_at", "scene_prompts", "image_status", "audio_status",
    "youtube_status", "youtube_video_id", "video_file_path", "error_message", "thumbnail_path"
]

VALID_TYPES = {"short", "horror_story", "confession_story"}


def col_letter(n):
    """Convert a 1-indexed column number to A1 notation letters (handles columns past Z)."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def ensure_headers(sheet):
    values = get_all_values(sheet)
    # No data rows beyond (or including) a header row means there is nothing real
    # to preserve -- this also covers sheets where Google Sheets auto-generated a
    # placeholder "Table" header row: in that case we overwrite it cleanly
    # instead of appending our headers after the junk.
    has_real_data = len(values) > 1
    if not has_real_data:
        end_col = col_letter(len(REQUIRED_HEADERS))
        run_with_retry("Writing Content headers", lambda: sheet.update(f"A1:{end_col}1", [REQUIRED_HEADERS], value_input_option="USER_ENTERED"))
        return REQUIRED_HEADERS, []
    headers = values[0]
    changed = False
    for h in REQUIRED_HEADERS:
        if h not in headers:
            headers.append(h)
            changed = True
    if changed:
        end_col = col_letter(len(headers))
        run_with_retry("Updating Content headers", lambda: sheet.update(f"A1:{end_col}1", [headers], value_input_option="USER_ENTERED"))
    return headers, values[1:]


def load_ideas():
    if not IDEAS_FILE.exists():
        raise FileNotFoundError(f"Ideas file not found: {IDEAS_FILE}")
    with IDEAS_FILE.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def make_unique_id(base, existing_ids):
    """Return base if unused, otherwise base-r2, base-r3, ... so we can recycle
    a finite pool of CSV ideas without ever colliding with ids already in use."""
    base = (base or "ND").strip()
    if base not in existing_ids:
        return base
    n = 2
    while f"{base}-r{n}" in existing_ids:
        n += 1
    return f"{base}-r{n}"


def build_row(item, headers):
    row = [""] * len(headers)
    for key, value in item.items():
        if key in headers:
            row[headers.index(key)] = str(value).strip()
    return row


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

    # Group available CSV ideas by type so we can top up each type's IDEA
    # backlog independently -- and recycle them with fresh ids once the finite
    # pool of CSV ids is exhausted, so a type can never permanently starve.
    ideas_by_type = {t: [] for t in VALID_TYPES}
    for item in load_ideas():
        vtype = (item.get("video_type") or "horror_story").strip().lower()
        if vtype in ideas_by_type:
            ideas_by_type[vtype].append(item)

    types_to_seed = sorted(VALID_TYPES) if requested_type == "all" else [requested_type]

    rows_to_append = []
    added_counts = {t: 0 for t in VALID_TYPES}
    for vtype in types_to_seed:
        items = ideas_by_type.get(vtype, [])
        if not items:
            print(f"Warning: no ideas of type '{vtype}' in {IDEAS_FILE}; skipping.")
            continue
        need = count_per_type - existing_idea_counts[vtype]
        for k in range(max(0, need)):
            item = items[k % len(items)]
            base_id = (item.get("id") or "").strip() or ("ND-" + vtype.upper())
            idea_id = make_unique_id(base_id, existing_ids)
      