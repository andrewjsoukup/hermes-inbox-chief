"""Small shared helpers: timestamps, business days, HTML → text, display names. No I/O."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .graph import address_of, when

UTC = timezone.utc


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_of(client: Any) -> datetime:
    """The mailbox's clock: a fixture pins ``now`` for deterministic tests; Graph uses real time."""
    pinned = parse_ts(getattr(client, "now", None) if isinstance(getattr(client, "now", None), str) else None)
    return pinned or datetime.now(UTC)


def now_iso(client: Any) -> str:
    return iso(now_of(client))


def business_days_between(start: datetime, end: datetime) -> int:
    """Weekdays after ``start``'s date up to and including ``end``'s date (live-system semantics)."""
    n, d = 0, start
    while d.date() < end.date():
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def add_business_days(start: datetime, days: int) -> datetime:
    d, left = start, max(0, days)
    while left:
        d += timedelta(days=1)
        if d.weekday() < 5:
            left -= 1
    return d


def clean(html: str | None) -> str:
    t = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", html or "")
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = re.sub(r"&nbsp;|&#160;", " ", t)
    t = t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'").replace("&quot;", '"')
    return re.sub(r"\s+", " ", t).strip()


def text_of(message: dict, *, unique: bool = True) -> str:
    """Readable body text: uniqueBody (just this message) when available, else body, else preview."""
    keys = ("uniqueBody", "body") if unique else ("body", "uniqueBody")
    for k in keys:
        content = (message.get(k) or {}).get("content")
        if content:
            return clean(content)
    return clean(message.get("bodyPreview") or "")


def who(recipient: Any) -> str:
    e = (recipient or {}).get("emailAddress") or {}
    name, addr = (e.get("name") or "").strip(), (e.get("address") or "").lower()
    return f"{name} <{addr}>" if name and name.lower() != addr else addr


def age_days(message: dict, now: datetime) -> float:
    t = parse_ts(when(message))
    return (now - t).total_seconds() / 86400 if t else 1e9


__all__ = ["address_of", "when", "parse_ts", "iso", "now_of", "now_iso", "business_days_between",
           "add_business_days", "clean", "text_of", "who", "age_days", "UTC"]
