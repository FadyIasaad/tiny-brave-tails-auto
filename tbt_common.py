import json
import os
import random
import time
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials as ServiceCredentials

SHEET_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

TRANSIENT_ERROR_TEXT = [
    "429", "500", "502", "503", "504", "timeout", "timed out",
    "temporarily", "temporary", "service unavailable", "internal error",
    "connection", "deadline exceeded", "rate limit", "malformed json",
    "invalid json", "unterminated string", "could not find json",
    "jsondecodeerror", "gemini returned empty text",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def is_retryable_error(error: Exception) -> bool:
    text = str(error).lower()
    return any(signal in text for signal in TRANSIENT_ERROR_TEXT)


def run_with_retry(action_name, func, max_attempts=5, max_wait_seconds=45):
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"{action_name}: attempt {attempt}/{max_attempts}")
            return func()
        except Exception as exc:
            last_error = exc
            if not is_retryable_error(exc) or attempt == max_attempts:
                raise
            wait_seconds = min(max_wait_seconds, (2 ** attempt) + random.uniform(0, 2.5))
            print(f"Temporary error during {action_name}: {exc}")
            print(f"Waiting {wait_seconds:.1f} seconds before retry...")
            time.sleep(wait_seconds)
    raise RuntimeError(f"{action_name} failed. Last error: {last_error}")


def get_sheets_client(service_account_json=None):
    raw_json = service_account_json or require_env("GOOGLE_SERVICE_ACCOUNT_JSON")
    try:
        service_account_info = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON.") from exc
    credentials = ServiceCredentials.from_service_account_info(service_account_info, scopes=SHEET_SCOPES)
    return gspread.authorize(credentials)


def open_spreadsheet(client, sheet_id=None):
    sid = sheet_id or require_env("GOOGLE_SHEET_ID")
    return run_with_retry("Opening Google Spreadsheet", lambda: client.open_by_key(sid))


def get_worksheet(spreadsheet, preferred_name="Content"):
    try:
        return run_with_retry(f"Opening worksheet {preferred_name}", lambda: spreadsheet.worksheet(preferred_name), max_attempts=3)
    except Exception:
        return run_with_retry("Opening first worksheet", lambda: spreadsheet.get_worksheet(0), max_attempts=3)



def get_or_create_worksheet(spreadsheet, title, rows=1000, cols=20):
    try:
        return run_with_retry(f"Opening worksheet {title}", lambda: spreadsheet.worksheet(title), max_attempts=3)
    except Exception:
        print(f"Worksheet {title} not found. Creating it.")
        return run_with_retry(f"Creating worksheet {title}", lambda: spreadsheet.add_worksheet(title=title, rows=rows, cols=cols), max_attempts=3)


def get_logs_worksheet(spreadsheet):
    sheet = get_or_create_worksheet(spreadsheet, "Logs", rows=1000, cols=4)
    values = get_all_values(sheet)
    if not values:
        run_with_retry("Writing Logs headers", lambda: sheet.update("A1:D1", [["timestamp", "video_id", "action", "message"]], value_input_option="USER_ENTERED"))
    return sheet

def get_all_values(sheet):
    return run_with_retry("Reading worksheet values", lambda: sheet.get_all_values())


def update_cell(sheet, row, col, value):
    return run_with_retry(f"Updating R{row}C{col}", lambda: sheet.update_cell(row, col, value))


def append_row(sheet, row):
    if sheet is None:
        print("LOG:", row)
        return None
    return run_with_retry("Appending log row", lambda: sheet.append_row(row, value_input_option="USER_ENTERED"))


def find_column(headers, name):
    if name not in headers:
        raise ValueError(f"Missing required column: {name}")
    return headers.index(name) + 1


def find_optional_column(headers, name):
    return headers.index(name) + 1 if name in headers else None


def get_cell(row, col):
    return row[col - 1].strip() if col and len(row) >= col and row[col - 1] is not None else ""


def update_optional(sheet, row_number, col, value):
    if col:
        update_cell(sheet, row_number, col, value)


def log(logs_sheet, video_id, action, message):
    append_row(logs_sheet, [utc_now(), video_id, action, str(message)[:1500]])
