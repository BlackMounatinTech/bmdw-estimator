"""Gmail send helper. Two paths supported — pick whichever you set up:

1. **SMTP via Gmail App Password** (RECOMMENDED — works on Render too)
   Set env vars:
     SMTP_USER=blackmountaindirtworks@gmail.com
     SMTP_PASSWORD=<16-char Google App Password>
   App Passwords: https://myaccount.google.com/apppasswords (requires 2FA on)
   No browser consent flow. Works locally AND on Render.

2. **OAuth Desktop App** (local-only — needs browser for first consent)
   Save downloaded client_secret.json as config/gmail_client_secret.json.
   Token gets cached at config/gmail_token.json after first run.
   Won't work from Render.

If both are configured, SMTP wins (simpler, more reliable).
"""

import base64
import mimetypes
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
CLIENT_SECRET_PATH = CONFIG_DIR / "gmail_client_secret.json"
TOKEN_PATH = CONFIG_DIR / "gmail_token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def _smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


def _oauth_configured() -> bool:
    return CLIENT_SECRET_PATH.exists()


def is_configured() -> bool:
    return _smtp_configured() or _oauth_configured()


def configured_method() -> str:
    """Returns 'smtp', 'oauth', or 'none' so the UI can show what's active."""
    if _smtp_configured():
        return "smtp"
    if _oauth_configured():
        return "oauth"
    return "none"


# ---- Shared message builder --------------------------------------------

def _build_email_message(sender: str, to: str, subject: str, body_text: str,
                         attachments: Optional[List[Path]] = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
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
    return msg


# ---- Path 1: SMTP (App Password) ---------------------------------------

def _send_via_smtp(to: str, subject: str, body_text: str,
                   attachments: Optional[List[Path]]) -> dict:
    sender = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "").strip()
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
    port = int(os.environ.get("SMTP_PORT", "465"))

    if not sender or not password:
        return {"ok": False, "reason": "SMTP_USER / SMTP_PASSWORD not set."}

    msg = _build_email_message(sender, to, subject, body_text, attachments)

    try:
        with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
        return {"ok": True, "method": "smtp", "to": to, "from": sender}
    except smtplib.SMTPAuthenticationError as exc:
        return {"ok": False, "method": "smtp",
                "reason": f"SMTP auth failed: {exc}. Check SMTP_USER + SMTP_PASSWORD "
                          "(must be a Google App Password, NOT your regular Gmail password)."}
    except Exception as exc:
        return {"ok": False, "method": "smtp", "reason": f"SMTP send failed: {exc}"}


# ---- Path 2: OAuth (local browser flow) --------------------------------

def _get_oauth_credentials():
    """Load cached creds or run the OAuth installed-app flow once.

    First call opens a browser for consent. Token is cached and refreshed
    silently for future calls.
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

    # First-time consent. Opens a local browser.
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_PATH.write_text(creds.to_json())
    return creds


def _send_via_oauth(to: str, subject: str, body_text: str,
                    attachments: Optional[List[Path]]) -> dict:
    try:
        from googleapiclient.discovery import build
        creds = _get_oauth_credentials()
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        msg = _build_email_message("me", to, subject, body_text, attachments)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        result = service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        return {"ok": True, "method": "oauth", "message_id": result.get("id"), "to": to}
    except Exception as exc:
        return {"ok": False, "method": "oauth", "reason": f"Gmail OAuth send failed: {exc}"}


# ---- Public API --------------------------------------------------------

def send_email(
    to: str,
    subject: str,
    body_text: str,
    attachments: Optional[List[Path]] = None,
) -> dict:
    """Send an email via the first configured method.

    Returns a status dict with at least {ok: bool}. Includes 'reason' on failure
    and 'method' indicating which path was used.
    """
    if not to:
        return {"ok": False, "reason": "No recipient email address."}

    if _smtp_configured():
        return _send_via_smtp(to, subject, body_text, attachments)

    if _oauth_configured():
        return _send_via_oauth(to, subject, body_text, attachments)

    return {
        "ok": False,
        "reason": (
            "Email sending is not configured. Set up either:\n"
            "  • SMTP App Password (recommended — works on Render): "
            "set SMTP_USER + SMTP_PASSWORD env vars.\n"
            "  • OAuth (local only): save Gmail OAuth client secret to "
            "config/gmail_client_secret.json.\n"
            "See workflows/setup_gmail.md for full instructions."
        ),
        "preview": {
            "to": to,
            "subject": subject,
            "body_chars": len(body_text),
            "attachments": [str(p) for p in (attachments or [])],
        },
    }
