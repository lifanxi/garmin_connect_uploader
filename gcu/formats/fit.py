from __future__ import annotations

import logging
import math
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from gcu.app.models import Track, TrackFile, TrackMetadata, TrackPoint
from gcu.formats.base import FormatOptions
from gcu.formats.city_resolver import resolve_display_city
from gcu.formats.timezone_resolver import resolve_display_timezone


class FitReader:
    format_id = "fit"

    def can_read(self, path: Path) -> bool:
        if path.suffix.lower() != ".fit":
            return False
        try:
            with path.open("rb") as file:
                header = file.read(12)
        except OSError:
            return False
        return len(header) >= 12 and header[8:12] == b".FIT"

    def read(self, path: Path, options: FormatOptions) -> TrackFile:
        try:
            from fit_tool.fit_file import FitFile
        except ImportError as exc:
            raise RuntimeError("FIT input requires fit-tool. Install it with: pip install fit-tool") from exc

        with _suppress_fit_tool_warnings():
            fit_file = FitFile.from_file(str(path))

        warnings: list[str] = []
        points: list[TrackPoint] = []
        source_device: str | None = "FIT"
        activity_bounds = _ActivityBounds()
        for index, record in enumerate(fit_file.records, start=1):
            message = record.message
            message_name = type(message).__name__
            if message_name == "FileIdMessage":
                source_device = _source_device(message)
                continue
            if message_name in {"EventMessage", "LapMessage", "SessionMessage", "ActivityMessage"}:
                activity_bounds.include(message)
                continue
            if message_name != "RecordMessage":
                continue
            try:
                points.append(_parse_record_message(message))
            except (TypeError, ValueError) as exc:
                warnings.append(f"record {index}: skipped invalid record: {exc}")

        points.sort(key=lambda point: (point.timestamp_utc, point.latitude, point.longitude))
        if not points:
            raise ValueError(f"No valid FIT track points found in {path}")

        first = points[0]
        last = points[-1]
        start_time_utc = activity_bounds.start_time_utc or first.timestamp_utc
        end_time_utc = activity_bounds.resolve_end_time(start_time_utc, last.timestamp_utc)
        duration_s = activity_bounds.resolve_duration_s(start_time_utc, end_time_utc)
        display_timezone_name = resolve_display_timezone(
            tuple(points),
            options.display_timezone_name,
            fallback_timezone=options.display_timezone_fallback,
        )
        display_city = resolve_display_city(
            tuple(points),
            options.display_city_name,
            min_population=options.display_city_min_population,
        )
        display_tz = ZoneInfo(display_timezone_name)
        metadata = TrackMetadata(
            start_time_utc=start_time_utc,
            end_time_utc=end_time_utc,
            duration_s=duration_s,
            point_count=len(points),
            start_latitude=first.latitude,
            start_longitude=first.longitude,
            end_latitude=last.latitude,
            end_longitude=last.longitude,
            display_name=_default_display_name(first.timestamp_utc, duration_s, display_tz, display_city),
            source_device=source_device,
            display_timezone=display_timezone_name,
            display_city=display_city,
        )
        return TrackFile(
            source_path=path,
            source_format=self.format_id,
            track=Track(points=tuple(points), metadata=metadata),
            warnings=tuple(warnings),
        )


def _parse_record_message(message: object) -> TrackPoint:
    timestamp = _parse_timestamp(_required_value(message, "timestamp"))
    latitude = _finite_float(_required_value(message, "position_lat"), "position_lat")
    longitude = _finite_float(_required_value(message, "position_long"), "position_long")
    return TrackPoint(
        timestamp_utc=timestamp,
        latitude=latitude,
        longitude=longitude,
        altitude_m=_optional_float(message, ("enhanced_altitude", "altitude")),
        speed_mps=_optional_float(message, ("enhanced_speed", "speed")),
        heading_deg=_optional_float(message, ("heading", "course")),
    )


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError("invalid timestamp")
        if value > 10_000_000_000:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        if value > 1_000_000_000:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        fit_epoch = datetime(1989, 12, 31, tzinfo=timezone.utc)
        return fit_epoch + timedelta(seconds=value)
    raise ValueError(f"unsupported timestamp value {value!r}")


@dataclass
class _ActivityBounds:
    start_time_utc: datetime | None = None
    end_time_utc: datetime | None = None
    elapsed_duration_s: float | None = None
    timer_duration_s: float | None = None

    def include(self, message: object) -> None:
        start_time = _optional_timestamp(message, "start_time")
        if start_time is not None and (
            self.start_time_utc is None or start_time < self.start_time_utc
        ):
            self.start_time_utc = start_time

        end_time = _optional_timestamp(message, "timestamp")
        if end_time is not None and (self.end_time_utc is None or end_time > self.end_time_utc):
            self.end_time_utc = end_time

        elapsed_duration = _optional_duration(message, "total_elapsed_time")
        if elapsed_duration is not None and (
            self.elapsed_duration_s is None or elapsed_duration > self.elapsed_duration_s
        ):
            self.elapsed_duration_s = elapsed_duration

        timer_duration = _optional_duration(message, "total_timer_time")
        if timer_duration is not None and (
            self.timer_duration_s is None or timer_duration > self.timer_duration_s
        ):
            self.timer_duration_s = timer_duration

    def resolve_end_time(self, start_time_utc: datetime, last_point_time_utc: datetime) -> datetime:
        candidates = [last_point_time_utc]
        if self.end_time_utc is not None:
            candidates.append(self.end_time_utc)
        if self.elapsed_duration_s is not None:
            candidates.append(start_time_utc + timedelta(seconds=self.elapsed_duration_s))
        return max(candidate for candidate in candidates if candidate >= start_time_utc)

    def resolve_duration_s(self, start_time_utc: datetime, end_time_utc: datetime) -> float:
        if self.timer_duration_s is not None:
            return self.timer_duration_s
        if self.elapsed_duration_s is not None:
            return self.elapsed_duration_s
        return max(0.0, (end_time_utc - start_time_utc).total_seconds())


def _optional_timestamp(message: object, name: str) -> datetime | None:
    value = getattr(message, name, None)
    if value is None:
        return None
    try:
        parsed = _parse_timestamp(value)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    if parsed.year < 1990:
        return None
    return parsed


def _optional_duration(message: object, name: str) -> float | None:
    value = getattr(message, name, None)
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _required_value(message: object, name: str) -> object:
    value = getattr(message, name, None)
    if value is None:
        raise ValueError(f"missing {name}")
    return value


def _optional_float(message: object, names: tuple[str, ...]) -> float | None:
    for name in names:
        value = getattr(message, name, None)
        if value is None:
            continue
        return _finite_float(value, name)
    return None


def _finite_float(value: object, name: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"invalid {name}")
    return parsed


def _default_display_name(
    start_utc: datetime,
    duration_s: float,
    display_tz: ZoneInfo,
    city_name: str | None,
) -> str:
    prefix = f"{city_name} " if city_name else ""
    return f"{prefix}Track Me"


def _source_device(message: object) -> str:
    manufacturer = getattr(message, "manufacturer", None)
    product = getattr(message, "product", None)
    serial_number = getattr(message, "serial_number", None)
    parts = ["FIT"]
    if manufacturer is not None:
        parts.append(f"manufacturer={manufacturer}")
    if product is not None:
        parts.append(f"product={product}")
    if serial_number is not None:
        parts.append(f"serial={serial_number}")
    return " ".join(parts)


@contextmanager
def _suppress_fit_tool_warnings() -> Iterator[None]:
    disabled_level = logging.root.manager.disable
    logging.disable(logging.WARNING)
    try:
        yield
    finally:
        logging.disable(disabled_level)
