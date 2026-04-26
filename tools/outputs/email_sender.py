"""Gmail send helper.

Setup:
1. Enable Gmail API in the BMDW Google Cloud project (same project as Sheets).
2. OAuth client (Desktop app) → download client_secret.json → save as
   config/gmail_client_secret.json.
3. First run will open a browser for Michael to grant access; the resulting
   token is saved to config/gmail_token.json.
4. The "from" address is whatever Google account is authenticated.

See workflows/setup_gmail.md for the full step-by-step.
"""

import base64
import mimetypes
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
CLIENT_SECRET_PATH = CONFIG_DIR / "gmail_client_secret.json"
TOKEN_PATH = CONFIG_DIR / "gmail_token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def is_configured() -> bool:
    return CLIENT_SECRET_PATH.exists()


def _get_credentials():
    """Load cached creds or run the OAuth installed-app flow once.

    First call (when no token.json) will open a browser. After Michael
    consents, the token is cached and refreshed silently for future calls.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json())
        return creds

    # First-time consent. Opens a local browser window.
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_PATH.write_text(creds.to_json())
    return creds


def _build_message(to: str, subject: str, body_text: str,
                   attachments: Optional[List[Path]] = None) -> dict:
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body_text)

    for path in (attachments or []):
        path = Path(path)
        if not path.exists():
            continue
        ctype, encoding = mimetypes.guess_type(str(path))
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {"raw": raw}


def send_email(
    to: str,
    subject: str,
    body_text: str,
    attachments: Optional[List[Path]] = None,
) -> dict:
    """Send an email via Gmail API. Returns a status dict."""
    if not is_configured():
        return {
            "ok": False,
            "reason": "Gmail not configured. See workflows/setup_gmail.md.",
            "preview": {
                "to": to,
                "subject": subject,
                "body_chars": len(body_text),
                "attachments": [str(p) for p in (attachments or [])],
            },
        }

    if not to:
        return {"ok": False, "reason": "No recipient email address."}

    try:
        from googleapiclient.discovery import build
        creds = _get_credentials()
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        message = _build_message(to, subject, body_text, attachments)
        result = service.users().messages().send(userId="me", body=message).execute()
        return {"ok": True, "message_id": result.get("id"), "to": to}
    except Exception as exc:
        return {"ok": False, "reason": f"Gmail send failed: {exc}"}
