from __future__ import annotations

from collections import Counter
from datetime import timedelta

from gcu.app.models import TrackPoint


def resolve_display_timezone(
    points: tuple[TrackPoint, ...],
    requested_timezone: str,
    fallback_timezone: str = "Asia/Shanghai",
    sample_minutes: int = 5,
) -> str:
    if requested_timezone != "auto":
        return requested_timezone
    if not points:
        return fallback_timezone

    try:
        from timezonefinder import TimezoneFinder
    except ImportError as exc:
        raise RuntimeError(
            "Automatic display timezone detection requires timezonefinder. "
            "Install it with: pip install timezonefinder"
        ) from exc

    first_time = points[0].timestamp_utc
    sample_until = first_time + timedelta(minutes=sample_minutes)
    sample = [point for point in points if point.timestamp_utc <= sample_until]
    if not sample:
        sample = [points[0]]

    finder = TimezoneFinder(in_memory=True)
    votes: Counter[str] = Counter()
    for point in sample:
        timezone_name = finder.timezone_at(lat=point.latitude, lng=point.longitude)
        if timezone_name:
            votes[timezone_name] += 1

    if not votes:
        return fallback_timezone
    return votes.most_common(1)[0][0]
