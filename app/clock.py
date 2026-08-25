from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current UTC time, naive.

    The DateTime columns in this schema are naive and hold UTC, so comparisons
    and defaults must be naive too. datetime.utcnow() is deprecated in 3.12,
    hence going through an aware value and dropping the tzinfo.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
