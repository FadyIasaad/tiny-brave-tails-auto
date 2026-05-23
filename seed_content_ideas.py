import csv
import json
import os
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
CONTENT_SHEET_NAME = "Content"
IDEAS_FILE = Path("content_100_view_ideas.csv")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

REQUIRED_HEADERS = ["id", "topic", "animal", "lesson", "status"]


def get_sheets_client():
    service_account_info = json.loads(SERVICE_ACCOUNT_JSON)
    credentials = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
    return gspread.authorize(credentials)


def main():
    client = get_sheets_client()
    spreadsheet = client.open_by_key(SHEET_ID)
    sheet = spreadsheet.worksheet(CONTENT_SHEET_NAME)
    values = sheet.get_all_values()
    if not values:
        raise ValueError("Content sheet is empty. Add headers first.")

    headers = values[0]
    for header in REQUIRED_HEADERS:
        if header not in headers:
            raise ValueError(f"Missing required column in Content sheet: {header}")

    id_col = headers.index("id")
    existing_ids = {row[id_col].strip() for row in values[1:] if len(row) > id_col and row[id_col].strip()}

    rows_to_append = []
    with IDEAS_FILE.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for item in reader:
            idea_id = item["id"].strip()
            if idea_id in existing_ids:
                continue
            row = [""] * len(headers)
            for key, value in item.items():
                if key in headers:
                    row[headers.index(key)] = value.strip()
            rows_to_append.append(row)

    if not rows_to_append:
        print("No new ideas to append. All IDs already exist.")
        return

    sheet.append_rows(rows_to_append, value_input_option="USER_ENTERED")
    print(f"Appended {len(rows_to_append)} new IDEA rows to Content.")


if __name__ == "__main__":
    main()
