"""
bot/utils/timezone.py
---------------------
Timezone utilities for the gold trading bot.

RULE: Store UTC everywhere, display ICT (Vietnam UTC+7) to users.

    Internal (DB, scheduler, API calls): use utc_now()
    Display  (Telegram, log, reports):   use fmt_ict() or to_ict()
"""

import calendar
from datetime import date as date_type
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Named timezone constants
# ---------------------------------------------------------------------------

ICT = timezone(timedelta(hours=7))   # Indochina Time — Vietnam (UTC+7)
UTC = timezone.utc


# ---------------------------------------------------------------------------
# Core converters
# ---------------------------------------------------------------------------

def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime.

    Use this everywhere in internal logic (schedulers, DB writes, API calls).
    """
    return datetime.now(UTC)


def to_ict(dt: datetime) -> datetime:
    """Convert a datetime to ICT (UTC+7).

    If *dt* is naive (no tzinfo) it is assumed to be UTC, which matches the
    bot's convention of storing all times in UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ICT)


def fmt_ict(dt: datetime, fmt: str = "%Y-%m-%d %H:%M ICT") -> str:
    """Format a datetime as an ICT string for display to users.

    Use this only when producing human-readable output (Telegram messages,
    log prefixes, report headers).  Never store the result in the DB.
    """
    return to_ict(dt).strftime(fmt)


# ---------------------------------------------------------------------------
# Date string helpers (UTC)
# ---------------------------------------------------------------------------

def today_utc() -> str:
    """Return today's date in UTC as 'YYYY-MM-DD'."""
    return datetime.now(UTC).strftime("%Y-%m-%d")


def month_start_utc() -> str:
    """Return the first day of the current UTC month as 'YYYY-MM-01'."""
    return datetime.now(UTC).strftime("%Y-%m-01")


# ---------------------------------------------------------------------------
# Session label
# ---------------------------------------------------------------------------

def session_label(utc_hour: int) -> str:
    """Map a UTC hour (0-23) to a human-readable trading session name in VN time.

    Returns a string like "Phiên London (14:00 VN)".
    """
    ict_hour = (utc_hour + 7) % 24

    if 1 <= utc_hour < 7:
        return f"Phiên Á/SGE ({ict_hour:02d}:00 VN)"
    elif 7 <= utc_hour < 12:
        return f"Phiên London ({ict_hour:02d}:00 VN)"
    elif 12 <= utc_hour < 20:
        return f"Phiên New York ({ict_hour:02d}:00 VN)"
    else:
        return f"Phiên chờ ({ict_hour:02d}:00 VN)"


# ---------------------------------------------------------------------------
# Range helpers
# ---------------------------------------------------------------------------

def days_in_range(date_from: str, date_to: str) -> int:
    """Return the number of calendar days between two ISO-8601 date strings (inclusive)."""
    d1 = date_type.fromisoformat(date_from)
    d2 = date_type.fromisoformat(date_to)
    return (d2 - d1).days + 1


def days_in_month(year_month: str) -> int:
    """Return the number of days in the given month.

    Args:
        year_month: 'YYYY-MM' string (e.g. '2026-03').
    """
    y, m = int(year_month[:4]), int(year_month[5:7])
    return calendar.monthrange(y, m)[1]
