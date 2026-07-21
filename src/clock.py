from datetime import datetime, timezone

_virtual_now: datetime | None = None


def get_now() -> datetime:
    if _virtual_now is not None:
        return _virtual_now
    return datetime.now(timezone.utc)


def set_virtual_time(dt: datetime) -> None:
    global _virtual_now
    _virtual_now = dt


def reset_clock() -> None:
    global _virtual_now
    _virtual_now = None