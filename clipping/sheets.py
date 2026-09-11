"""Write the report into a Google Sheet.

Authenticates as a service account, which is a robot Google account with its own email
address. The target sheet must be shared with that address, or every call fails. That is
the single most common setup mistake, so the error says so explicitly.
"""

import json
from pathlib import Path

from clipping import store

POSTS_TAB = "Posts"
CAMPAIGNS_TAB = "Campaigns"
CAMPAIGN_COLUMNS = ("campaign", "posts", "creators", "reach", "likes", "comments")


class SheetsError(Exception):
    """Export could not complete. The message says what to fix."""


def service_account_email(credentials_path: str) -> str | None:
    """The robot address the sheet must be shared with. Surfaced to save a hunt."""
    try:
        return json.loads(Path(credentials_path).read_text(encoding="utf-8")).get(
            "client_email"
        )
    except (OSError, ValueError):
        return None


def _client(credentials_path: str):
    try:
        import gspread
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise SheetsError(
            'Google Sheets support is not installed. Run: pip install -e ".[sheets]"'
        ) from exc

    path = Path(credentials_path or "")
    if not path.exists():
        raise SheetsError(
            f"Service account file not found at '{path}'. Download the JSON key from "
            "your Google Cloud project and point GOOGLE_SERVICE_ACCOUNT_JSON at it."
        )
    try:
        return gspread.service_account(filename=str(path))
    except Exception as exc:
        raise SheetsError(f"Could not read the service account file: {exc}") from exc


def _replace_tab(spreadsheet, title: str, header: tuple, rows: list[list]) -> None:
    try:
        worksheet = spreadsheet.worksheet(title)
        worksheet.clear()
    except Exception:
        worksheet = spreadsheet.add_worksheet(
            title=title, rows=max(len(rows) + 10, 100), cols=max(len(header), 10)
        )
    worksheet.update([list(header)] + rows, value_input_option="RAW")
    worksheet.freeze(rows=1)


def push(conn, sheet_id: str, credentials_path: str) -> dict:
    """Replace the Posts and Campaigns tabs with current data. Returns a short summary."""
    if not sheet_id:
        raise SheetsError("No sheet configured. Set GOOGLE_SHEET_ID in .env.")

    client = _client(credentials_path)
    try:
        spreadsheet = client.open_by_key(sheet_id)
    except Exception as exc:
        email = service_account_email(credentials_path) or "the service account"
        raise SheetsError(
            f"Could not open sheet {sheet_id}: {exc}. Share the sheet with {email} "
            "and give it Editor access."
        ) from exc

    posts = store.report_rows(conn)
    campaigns = store.campaign_totals(conn)

    _replace_tab(
        spreadsheet, POSTS_TAB, store.REPORT_COLUMNS,
        [[r[c] for c in store.REPORT_COLUMNS] for r in posts],
    )
    _replace_tab(
        spreadsheet, CAMPAIGNS_TAB, CAMPAIGN_COLUMNS,
        [[r.get(c, "") for c in CAMPAIGN_COLUMNS] for r in campaigns],
    )

    return {
        "posts": len(posts),
        "campaigns": len(campaigns),
        "url": f"https://docs.google.com/spreadsheets/d/{sheet_id}",
        "title": spreadsheet.title,
    }
