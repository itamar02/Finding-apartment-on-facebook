"""Google Calendar: one event per confirmed apartment viewing. Nothing is created without a Confirm tap.

Setup once on the PC (see README, "Google Calendar"):
  1. In Google Cloud Console create an OAuth client of type "Desktop app" for the Calendar
     API and save its JSON as secrets/google_client_secret.json.
  2. python app_server.py --google-login   (opens a browser on the PC to allow access)
The access token is stored in secrets/google_token.json (git-ignored). Only the
calendar.events scope is requested: the app can add events, nothing else.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

SECRETS_DIR = Path(__file__).resolve().parent.parent / "secrets"
CLIENT_FILE = SECRETS_DIR / "google_client_secret.json"
TOKEN_FILE = SECRETS_DIR / "google_token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
TIMEZONE = "Asia/Jerusalem"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


class CalendarError(Exception):
    pass


def status():
    return {"client_file": CLIENT_FILE.exists(), "signed_in": TOKEN_FILE.exists()}


def login():
    """Run Google's sign-in in a browser on this PC and store the token."""
    if not CLIENT_FILE.exists():
        raise CalendarError(f"{CLIENT_FILE.name} not found in secrets/ - see the README, 'Google Calendar'")
    from google_auth_oauthlib.flow import InstalledAppFlow
    creds = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES).run_local_server(port=0)
    SECRETS_DIR.mkdir(exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")


def _session():
    if not TOKEN_FILE.exists():
        raise CalendarError("Google Calendar is not connected: run python app_server.py --google-login on the PC")
    from google.auth.transport.requests import AuthorizedSession, Request
    from google.oauth2.credentials import Credentials
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return AuthorizedSession(creds)


def build_event(summary, start, minutes, location=None, description=None):
    """The Calendar API body; start is a naive local datetime (Israel time)."""
    end = start + timedelta(minutes=minutes)
    return {
        "summary": summary,
        "location": location or "",
        "description": description or "",
        "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:00"), "timeZone": TIMEZONE},
        "end": {"dateTime": end.strftime("%Y-%m-%dT%H:%M:00"), "timeZone": TIMEZONE},
        "reminders": {"useDefault": True},
    }


def create_event(event, session=None):
    """Create the event. Returns (event id, link to it in Google Calendar)."""
    session = session or _session()
    resp = session.post(EVENTS_URL, data=json.dumps(event), headers={"Content-Type": "application/json"})
    if resp.status_code >= 300:
        raise CalendarError(f"Google Calendar refused the event: HTTP {resp.status_code} {resp.text[:200]}")
    body = resp.json()
    return body["id"], body.get("htmlLink")


def parse_local(date, time):
    try:
        return datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        raise CalendarError("date must be YYYY-MM-DD and time HH:MM")
