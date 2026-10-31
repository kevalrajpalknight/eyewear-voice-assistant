"""Timestamps with the zone always explicit: UTC and the machine's local time (offset shown)."""

from datetime import datetime, timezone


def stamps(moment: datetime | None = None) -> dict[str, str]:
    """{"ts": UTC ISO (+00:00), "local": local ISO with its own offset}, millisecond precision."""
    now = moment or datetime.now(timezone.utc)
    utc = now.astimezone(timezone.utc)
    return {
        "ts": utc.isoformat(timespec="milliseconds"),
        "local": utc.astimezone().isoformat(timespec="milliseconds"),
    }


def both(moment: datetime) -> str:
    """Human-readable 'HH:MM:SS.mmmZ / HH:MM:SS.mmm+05:30' for console output."""
    utc = moment.astimezone(timezone.utc)
    local = utc.astimezone()
    offset = local.strftime("%z")
    offset = f"{offset[:3]}:{offset[3:]}"
    return f"{utc.strftime('%H:%M:%S.%f')[:-3]}Z / {local.strftime('%H:%M:%S.%f')[:-3]}{offset}"
