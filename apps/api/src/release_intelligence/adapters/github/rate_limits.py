from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

SECONDARY_RATE_LIMIT_PREFIXES = (
    "you have exceeded a secondary rate limit",
    "you have triggered an abuse detection mechanism",
)


def reset_at(headers: Mapping[str, str]) -> datetime | None:
    raw = headers.get("X-RateLimit-Reset")
    try:
        seconds = int(raw) if raw is not None else None
        if seconds is None or seconds < 0:
            return None
        return datetime.fromtimestamp(seconds, UTC)
    except (ValueError, OverflowError, OSError):
        return None


def valid_retry_after(value: str | None) -> bool:
    if value is None:
        return False
    if value.isascii() and value.isdigit():
        return True
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return parsed.tzinfo is not None


def secondary_rate_limit(payload: object) -> bool:
    if not isinstance(payload, Mapping):
        return False
    message = payload.get("message")
    if not isinstance(message, str):
        return False
    normalized = " ".join(message.casefold().split())
    return any(normalized.startswith(prefix) for prefix in SECONDARY_RATE_LIMIT_PREFIXES)


def is_rate_limited(
    status_code: int, headers: Mapping[str, str], payload: object
) -> bool:
    if status_code == 429:
        return True
    if status_code != 403:
        return False
    return (
        headers.get("X-RateLimit-Remaining") == "0"
        or valid_retry_after(headers.get("Retry-After"))
        or secondary_rate_limit(payload)
    )
