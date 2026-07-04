from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

from gcu.app.models import Track


_FIT_ALTITUDE_SCALE = 5.0
_FIT_ALTITUDE_OFFSET = 500.0
_FIT_ALTITUDE_MIN_M = -_FIT_ALTITUDE_OFFSET
_FIT_ALTITUDE_LEGACY_MAX_M = 65535 / _FIT_ALTITUDE_SCALE - _FIT_ALTITUDE_OFFSET


def _safe_altitude_value(altitude_m: float | None) -> float | None:
    """Return altitude if it can be safely encoded into FIT altitude fields."""
    if altitude_m is None or not math.isfinite(altitude_m):
        return None

    # FIT altitude fields use UINT16/UINT32 with scale=5 and offset=500, so the
    # encoded raw value must be >= 0. Values below -500m become negative and
    # would make fit-tool reject this record when building the FIT file.
    if (altitude_m + _FIT_ALTITUDE_OFFSET) * _FIT_ALTITUDE_SCALE < 0:
        return None

    return altitude_m


def write_fit(track: Track, output_path: Path, activity_name: str | None = None) -> Path:
    try:
        from fit_tool.fit_file_builder import FitFileBuilder
        from fit_tool.profile.messages.activity_message import ActivityMessage
        from fit_tool.profile.messages.event_message import EventMessage
        from fit_tool.profile.messages.file_creator_message import FileCreatorMessage
        from fit_tool.profile.messages.file_id_message import FileIdMessage
        from fit_tool.profile.messages.lap_message import LapMessage
        from fit_tool.profile.messages.record_message import RecordMessage
        from fit_tool.profile.messages.session_message import SessionMessage
        from fit_tool.profile.profile_type import Event, EventType, FileType, Manufacturer, Sport, SubSport
    except ImportError as exc:
        raise RuntimeError("FIT export requires fit-tool. Install it with: pip install fit-tool") from exc

    points = track.points
    if not points:
        raise ValueError("Cannot write FIT for an empty track")

    metadata = track.metadata
    name = activity_name or metadata.display_name
    builder = FitFileBuilder(auto_define=True)
    max_speed_mps = 65535 / 1000

    file_id = FileIdMessage()
    file_id.type = FileType.ACTIVITY.value
    file_id.manufacturer = Manufacturer.HOLUX.value
    file_id.product = 0
    file_id.serial_number = 0x12345678
    file_id.time_created = int(datetime.now().timestamp() * 1000)
    builder.add(file_id)

    creator = FileCreatorMessage()
    creator.software_version = 1
    creator.hardware_version = 1
    builder.add(creator)

    add_record = builder.add
    record_cls = RecordMessage
    for point in points:
        record = record_cls()
        record.timestamp = int(point.timestamp_utc.timestamp() * 1000)
        record.position_lat = point.latitude
        record.position_long = point.longitude

        altitude_m = _safe_altitude_value(point.altitude_m)
        if altitude_m is not None:
            record.enhanced_altitude = altitude_m
            if _FIT_ALTITUDE_MIN_M <= altitude_m <= _FIT_ALTITUDE_LEGACY_MAX_M:
                record.altitude = altitude_m

        speed_mps = point.speed_mps
        if speed_mps is not None:
            if 0 <= speed_mps <= max_speed_mps:
                record.speed = speed_mps
            else:
                record.enhanced_speed = speed_mps
        add_record(record)

    start_ms = int(metadata.start_time_utc.timestamp() * 1000)
    end_ms = int(metadata.end_time_utc.timestamp() * 1000)

    event = EventMessage()
    event.timestamp = start_ms
    event.event = Event.TIMER.value
    event.event_type = EventType.START.value
    event.event_group = 0
    builder.add(event)

    lap = LapMessage()
    lap.timestamp = end_ms
    lap.start_time = start_ms
    lap.total_elapsed_time = metadata.duration_s
    lap.total_timer_time = metadata.duration_s
    lap.start_position_lat = metadata.start_latitude
    lap.start_position_long = metadata.start_longitude
    lap.end_position_lat = metadata.end_latitude
    lap.end_position_long = metadata.end_longitude
    lap.wkt_step_name = name
    builder.add(lap)

    session = SessionMessage()
    session.timestamp = end_ms
    session.start_time = start_ms
    session.total_elapsed_time = metadata.duration_s
    session.total_timer_time = metadata.duration_s
    session.sport = Sport.GENERIC.value
    session.sub_sport = SubSport.TRACK_ME.value
    session.first_lap_index = 0
    session.num_laps = 1
    session.name = name
    builder.add(session)

    activity = ActivityMessage()
    activity.timestamp = end_ms
    activity.total_timer_time = metadata.duration_s
    activity.num_sessions = 1
    activity.type = 0
    activity.event = 26
    activity.event_type = 1
    builder.add(activity)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fit_file = builder.build()
    fit_file.to_file(str(output_path))
    return output_path
