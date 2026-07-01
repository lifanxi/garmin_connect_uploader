from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from gcu.app.models import LocalTrack, RemoteActivity
from gcu.duplicate.remote_index import RemoteActivityIndex


@dataclass(frozen=True)
class MatchOptions:
    coord_tolerance_deg: float = 0.001
    time_tolerance_s: int = 60
    duration_tolerance_s: int = 120


def find_legacy_matches(
    local_track: LocalTrack,
    index: RemoteActivityIndex,
    options: MatchOptions,
) -> tuple[RemoteActivity, ...]:
    metadata = local_track.track_file.track.metadata
    local_start_ms = int(metadata.start_time_utc.timestamp() * 1000)
    local_date = metadata.start_time_utc.date()
    candidate_dates = {
        local_date - timedelta(days=1),
        local_date,
        local_date + timedelta(days=1),
    }
    candidates: list[RemoteActivity] = []
    for candidate_date in candidate_dates:
        candidates.extend(index.by_date.get(candidate_date, []))

    matches: list[RemoteActivity] = []
    for activity in candidates:
        if activity.begin_timestamp_ms is None:
            continue
        if activity.start_latitude is None or activity.start_longitude is None:
            continue
        time_diff_s = abs(activity.begin_timestamp_ms - local_start_ms) / 1000
        lat_diff = abs(activity.start_latitude - metadata.start_latitude)
        lon_diff = abs(activity.start_longitude - metadata.start_longitude)
        if time_diff_s > options.time_tolerance_s:
            continue
        if lat_diff > options.coord_tolerance_deg or lon_diff > options.coord_tolerance_deg:
            continue
        if activity.duration_s is not None:
            duration_diff_s = abs(activity.duration_s - metadata.duration_s)
            if duration_diff_s > options.duration_tolerance_s:
                continue
        matches.append(activity)
    return tuple(matches)
