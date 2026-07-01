from __future__ import annotations

from zoneinfo import ZoneInfo

from gcu.app.models import Track
from gcu.duplicate.fingerprint import append_or_replace_token


def planned_activity_name(track: Track, token: str, template: str | None = None) -> str:
    metadata = track.metadata
    display_tz = ZoneInfo(metadata.display_timezone)
    if template:
        base = template.format(
            date=metadata.start_time_utc.astimezone(display_tz).strftime("%b %d"),
            duration=_format_duration(metadata.duration_s),
            start=metadata.start_time_utc.isoformat(),
            end=metadata.end_time_utc.isoformat(),
            points=metadata.point_count,
            city=metadata.display_city or "",
        )
    else:
        base = metadata.display_name
    return append_or_replace_token(base, token)


def _format_duration(duration_s: float) -> str:
    total_minutes = max(0, int(duration_s // 60))
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"
