from __future__ import annotations

import csv
from datetime import datetime, timezone
from itertools import chain
from pathlib import Path
from zoneinfo import ZoneInfo

from gcu.app.models import Track, TrackFile, TrackMetadata, TrackPoint
from gcu.formats.base import FormatOptions
from gcu.formats.city_resolver import resolve_display_place
from gcu.formats.timezone_resolver import resolve_display_timezone


class ColumbusCsvReader:
    format_id = "columbus-csv"
    expected_header = (
        "INDEX",
        "TAG",
        "DATE",
        "TIME",
        "LATITUDE N/S",
        "LONGITUDE E/W",
        "HEIGHT",
        "SPEED",
        "HEADING",
    )

    def can_read(self, path: Path) -> bool:
        if path.suffix.lower() != ".csv":
            return False
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                row = next(csv.reader(handle), None)
        except OSError:
            return False
        return self._is_expected_header(row) or self._looks_like_data_row(row)

    def read(self, path: Path, options: FormatOptions) -> TrackFile:
        warnings: list[str] = []
        points: list[TrackPoint] = []
        source_tz = ZoneInfo(options.timezone_name)

        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            first_row = next(reader, None)
            if self._is_expected_header(first_row):
                rows = reader
                start_line = 2
            else:
                rows = chain([first_row], reader)
                start_line = 1

            for line_number, row in enumerate(rows, start=start_line):
                try:
                    point = self._parse_row(self._row_to_dict(row), source_tz)
                except (KeyError, TypeError, ValueError) as exc:
                    warnings.append(f"line {line_number}: skipped invalid row: {exc}")
                    continue
                points.append(point)

        points.sort(key=lambda point: (point.timestamp_utc, point.latitude, point.longitude))
        if not points:
            raise ValueError(f"No valid track points found in {path}")

        first = points[0]
        last = points[-1]
        duration_s = (last.timestamp_utc - first.timestamp_utc).total_seconds()
        display_timezone_name = resolve_display_timezone(
            tuple(points),
            options.display_timezone_name,
            fallback_timezone=options.display_timezone_fallback,
        )
        display_place = resolve_display_place(
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
            display_name=self._default_display_name(first.timestamp_utc, duration_s, display_tz, display_place.city),
            source_device="Columbus",
            display_timezone=display_timezone_name,
            display_city=display_place.city,
            display_country=display_place.country,
            display_state=display_place.state,
        )
        return TrackFile(
            source_path=path,
            source_format=self.format_id,
            track=Track(points=tuple(points), metadata=metadata),
            warnings=tuple(warnings),
        )

    def _is_expected_header(self, row: list[str] | None) -> bool:
        return tuple((cell or "").strip() for cell in (row or ()))[:9] == self.expected_header

    def _looks_like_data_row(self, row: list[str] | None) -> bool:
        if row is None or len(row) < len(self.expected_header):
            return False
        values = [(cell or "").strip() for cell in row]
        try:
            self._parse_datetime(values[2], values[3], ZoneInfo("UTC"))
            self._parse_coordinate(values[4])
            self._parse_coordinate(values[5])
            for value in values[6:9]:
                if value:
                    float(value)
        except ValueError:
            return False
        return True

    def _row_to_dict(self, row: list[str] | None) -> dict[str, str]:
        values = row or []
        return {
            header: values[index] if index < len(values) else ""
            for index, header in enumerate(self.expected_header)
        }

    def _parse_row(self, row: dict[str, str], source_tz: ZoneInfo) -> TrackPoint:
        local_dt = self._parse_datetime(row["DATE"].strip(), row["TIME"].strip(), source_tz)
        return TrackPoint(
            timestamp_utc=local_dt.astimezone(timezone.utc),
            latitude=self._parse_coordinate(row["LATITUDE N/S"].strip()),
            longitude=self._parse_coordinate(row["LONGITUDE E/W"].strip()),
            altitude_m=float(row["HEIGHT"]) if row.get("HEIGHT") not in (None, "") else None,
            speed_mps=self._parse_speed_mps(row.get("SPEED")),
            heading_deg=float(row["HEADING"]) if row.get("HEADING") not in (None, "") else None,
        )

    def _parse_datetime(self, date_value: str, time_value: str, source_tz: ZoneInfo) -> datetime:
        if len(date_value) != 6 or len(time_value) != 6:
            raise ValueError("DATE and TIME must use YYMMDD and HHMMSS")
        year = 2000 + int(date_value[0:2])
        month = int(date_value[2:4])
        day = int(date_value[4:6])
        hour = int(time_value[0:2])
        minute = int(time_value[2:4])
        second = int(time_value[4:6])
        return datetime(year, month, day, hour, minute, second, tzinfo=source_tz)

    def _parse_coordinate(self, value: str) -> float:
        if not value:
            raise ValueError("empty coordinate")
        direction = value[-1].upper()
        if direction not in "NSEW":
            raise ValueError(f"coordinate lacks hemisphere suffix: {value}")
        degrees = float(value[:-1])
        return -degrees if direction in "SW" else degrees

    def _kmh_to_mps(self, speed_kmh: float) -> float:
        return speed_kmh / 3.6

    def _parse_speed_mps(self, value: str | None) -> float | None:
        if value in (None, ""):
            return None
        speed_kmh = float(value)
        if speed_kmh < 0:
            return None
        return self._kmh_to_mps(speed_kmh)

    def _default_display_name(
        self,
        start_utc: datetime,
        duration_s: float,
        source_tz: ZoneInfo,
        city_name: str | None,
    ) -> str:
        prefix = f"{city_name} " if city_name else ""
        return f"{prefix}Track Me"
