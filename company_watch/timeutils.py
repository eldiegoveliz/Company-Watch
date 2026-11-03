from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo


TZ_ALIASES = {
    "UTC": UTC,
    "ET": ZoneInfo("America/New_York"),
    "EST": ZoneInfo("America/New_York"),
    "EDT": ZoneInfo("America/New_York"),
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


def parse_datetime(value: str, default_tz: str = "UTC") -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_ALIASES.get(default_tz.upper(), UTC))
    return dt.astimezone(UTC)


def parse_loose_datetime(value: str | None, default_tz: str = "UTC") -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return parse_datetime(raw, default_tz=default_tz)
    except Exception:
        pass
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_ALIASES.get(default_tz.upper(), UTC))
        return dt.astimezone(UTC)
    except Exception:
        return None


def parse_duration(value: str) -> timedelta:
    raw = value.strip().lower()
    if raw.endswith("min"):
        return timedelta(minutes=float(raw[:-3]))
    if raw.endswith("m"):
        return timedelta(minutes=float(raw[:-1]))
    if raw.endswith("h"):
        return timedelta(hours=float(raw[:-1]))
    if raw.endswith("d"):
        return timedelta(days=float(raw[:-1]))
    return timedelta(seconds=float(raw))
