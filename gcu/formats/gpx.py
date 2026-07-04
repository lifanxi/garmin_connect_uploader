from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from gcu.app.models import Track, TrackFile, TrackMetadata, TrackPoint
from gcu.formats.base import FormatOptions
from gcu.formats.city_resolver import resolve_display_city
from gcu.formats.timezone_resolver import resolve_display_timezone


class GpxReader:
    format_id = "gpx"

    def can_read(self, path: Path) -> bool:
        if path.suffix.lower() != ".gpx":
            return False
        try:
            for _event, element in ET.iterparse(path, events=("start",)):
                return _local_name(element.tag) == "gpx"
        except (ET.ParseError, OSError):
            return False
        return False

    def read(self, path: Path, options: FormatOptions) -> TrackFile:
        warnings: list[str] = []
        points: list[TrackPoint] = []
        source_tz = ZoneInfo(options.timezone_name)

        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            raise ValueError(f"Invalid GPX XML in {path}: {exc}") from exc

        for index, element in enumerate(root.iter(), start=1):
            if _local_name(element.tag) != "trkpt":
                continue
            try:
                points.append(self._parse_track_point(element, source_tz))
            except (TypeError, ValueError) as exc:
                warnings.append(f"track point {index}: skipped invalid point: {exc}")

        points.sort(key=lambda point: (point.timestamp_utc, point.latitude, point.longitude))
        if not points:
            raise ValueError(f"No valid GPX track points found in {path}")

        first = points[0]
        last = points[-1]
        duration_s = (last.timestamp_utc - first.timestamp_utc).total_seconds()
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
            start_time_utc=first.timestamp_utc,
            end_time_utc=last.timestamp_utc,
            duration_s=duration_s,
            point_count=len(points),
            start_latitude=first.latitude,
            start_longitude=first.longitude,
            end_latitude=last.latitude,
            end_longitude=last.longitude,
            display_name=self._default_display_name(first.timestamp_utc, duration_s, display_tz, display_city),
            source_device=_creator(root),
            display_timezone=display_timezone_name,
            display_city=display_city,
        )
        return TrackFile(
            source_path=path,
            source_format=self.format_id,
            track=Track(points=tuple(points), metadata=metadata),
            warnings=tuple(warnings),
        )

    def _parse_track_point(self, element: ET.Element, source_tz: ZoneInfo) -> TrackPoint:
        latitude = float(_required_attr(element, "lat"))
        longitude = float(_required_attr(element, "lon"))
        time_text = _required_child_text(element, "time")
        altitude_text = _optional_child_text(element, "ele")
        extension_values = _extension_values(element)
        return TrackPoint(
            timestamp_utc=self._parse_datetime(time_text, source_tz),
            latitude=latitude,
            longitude=longitude,
            altitude_m=float(altitude_text) if altitude_text not in (None, "") else None,
            speed_mps=_float_or_none(extension_values.get("speed")),
            heading_deg=_float_or_none(extension_values.get("course") or extension_values.get("heading")),
            accuracy_m=_float_or_none(extension_values.get("accuracy") or extension_values.get("hacc")),
            raw_extensions=extension_values,
        )

    def _parse_datetime(self, value: str, source_tz: ZoneInfo) -> datetime:
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=source_tz)
        return parsed.astimezone(timezone.utc)

    def _default_display_name(
        self,
        start_utc: datetime,
        duration_s: float,
        display_tz: ZoneInfo,
        city_name: str | None,
    ) -> str:
        prefix = f"{city_name} " if city_name else ""
        return f"{prefix}Track Me"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _creator(root: ET.Element) -> str | None:
    value = root.attrib.get("creator")
    return value.strip() if value else "GPX"


def _required_attr(element: ET.Element, name: str) -> str:
    value = element.attrib.get(name)
    if value in (None, ""):
        raise ValueError(f"missing {name}")
    return value


def _required_child_text(element: ET.Element, name: str) -> str:
    value = _optional_child_text(element, name)
    if value in (None, ""):
        raise ValueError(f"missing {name}")
    return value


def _optional_child_text(element: ET.Element, name: str) -> str | None:
    for child in element:
        if _local_name(child.tag) == name:
            return (child.text or "").strip()
    return None


def _extension_values(element: ET.Element) -> dict[str, str]:
    values: dict[str, str] = {}
    for child in element.iter():
        name = _local_name(child.tag)
        if name in {"speed", "course", "heading", "accuracy", "hacc"} and child.text:
            values[name] = child.text.strip()
    return values


def _float_or_none(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    return float(value)
