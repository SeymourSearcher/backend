from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current UTC time, naive.

    The DateTime columns in this schema are naive and hold UTC, so comparisons
    and defaults must be naive too. datetime.utcnow() is deprecated in 3.12,
    hence going through an aware value and dropping the tzinfo.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utcnow_ms() -> int:
    """Epoch milliseconds, the unit the Hypixel auction timestamps use.

    Built from the aware value, not from utcnow(): .timestamp() on a naive
    datetime reads it as local time, which would shift the result by the host's
    offset from UTC.
    """
    return int(datetime.now(timezone.utc).timestamp() * 1000)
