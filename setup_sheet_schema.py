from tbt_common import get_sheets_client, open_spreadsheet, get_worksheet, get_all_values, update_cell, run_with_retry

CONTENT_SHEET_NAME = "Content"
LOGS_SHEET_NAME = "Logs"

REQUIRED_HEADERS = [
    "id", "topic", "animal", "lesson", "video_type", "target_minutes", "main_character", "story_universe", "audience", "made_for_kids",
    "script", "title", "description", "status", "video_url", "created_at", "scene_prompts", "image_status", "audio_status",
    "youtube_status", "youtube_video_id", "video_file_path", "error_message"
]

STARTER_ROWS = [
    ["TBT-SHORT-001", "Toby finds a cracked lantern beside the river and must decide whether to carry it alone", "old turtle", "Small courage matters when nobody is watching", "short", 1, "Toby", "The Moonlit Forest", "general audience - not made for kids", "FALSE", "", "", "", "IDEA", "", "", "", "", "", "", "", "", ""],
    ["TBT-BED-001", "Toby walks through a quiet rainy forest to return a forgotten bell before sunrise", "old turtle", "Peace is earned through patience, not escape", "bedtime", 30, "Toby", "The Moonlit Forest", "general audience - not made for kids", "FALSE", "", "", "", "IDEA", "", "", "", "", "", "", "", "", ""],
    ["TBT-LONG-001", "Toby carries a lantern through the flooded Moonlit Forest to find the animal who stopped answering", "old turtle", "Some hearts heal only when they stop pretending they are fine", "long_story", 30, "Toby", "The Moonlit Forest", "general audience - not made for kids", "FALSE", "", "", "", "IDEA", "", "", "", "", "", "", "", "", ""],
    ["TBT-COL-001", "Five quiet nights where Toby helps animals who are hiding old pain", "turtle collection", "Kindness becomes powerful when it costs something", "toby_collection", 45, "Toby", "The Moonlit Forest", "general audience - not made for kids", "FALSE", "", "", "", "IDEA", "", "", "", "", "", "", "", "", ""],
    ["TBT-CALM-001", "Toby sits beside the lake while the forest slowly remembers how to breathe", "old turtle", "Stillness can be a form of strength", "calming", 30, "Toby", "The Moonlit Forest", "general audience - not made for kids", "FALSE", "", "", "", "IDEA", "", "", "", "", "", "", "", "", ""],
    ["TBT-ADV-001", "Toby crosses the broken bridge to save a wolf everyone else feared", "old turtle and wolf", "Bravery is not loud; it keeps walking", "adventure", 30, "Toby", "The Moonlit Forest", "general audience - not made for kids", "FALSE", "", "", "", "IDEA", "", "", "", "", "", "", "", "", ""],
]


def ensure_headers(sheet):
    values = get_all_values(sheet)
    if not values:
        run_with_retry("Writing header row", lambda: sheet.update("A1:W1", [REQUIRED_HEADERS], value_input_option="USER_ENTERED"))
        return REQUIRED_HEADERS
    headers = values[0]
    changed = False
    for h in REQUIRED_HEADERS:
        if h not in headers:
            headers.append(h)
            changed = True
    if changed:
        end_col = chr(ord('A') + len(headers) - 1) if len(headers) <= 26 else 'W'
        run_with_retry("Updating header row", lambda: sheet.update(f"A1:{end_col}1", [headers], value_input_option="USER_ENTERED"))
    return headers


def main():
    client = get_sheets_client()
    spreadsheet = open_spreadsheet(client)
    content = get_worksheet(spreadsheet, CONTENT_SHEET_NAME)
    headers = ensure_headers(content)
    values = get_all_values(content)
    if len(values) <= 1:
        run_with_retry("Adding starter long-video rows", lambda: content.append_rows(STARTER_ROWS, value_input_option="USER_ENTERED"))
    try:
        spreadsheet.worksheet(LOGS_SHEET_NAME)
    except Exception:
        logs = spreadsheet.add_worksheet(title=LOGS_SHEET_NAME, rows=1000, cols=4)
        logs.update("A1:D1", [["timestamp", "video_id", "action", "message"]], value_input_option="USER_ENTERED")
    print("Sheet schema ready. Next run: Generate Content Backlog to fill ideas for every video type.")

if __name__ == "__main__":
    main()
