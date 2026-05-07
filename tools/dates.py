"""Date/time helpers — anchor to Pacific time so dates match Michael's
local calendar regardless of where the server runs (Render is UTC, which
flips dates 4-7 hours early)."""

from datetime import date, datetime
try:
    from zoneinfo import ZoneInfo
    _PACIFIC = ZoneInfo("America/Vancouver")
except Exception:
    _PACIFIC = None


def pacific_now() -> datetime:
    if _PACIFIC is not None:
        return datetime.now(_PACIFIC)
    return datetime.now()


def pacific_today() -> date:
    return pacific_now().date()


def pacific_iso(timespec: str = "seconds") -> str:
    """ISO-format timestamp in Pacific time, with TZ offset."""
    return pacific_now().isoformat(timespec=timespec)
