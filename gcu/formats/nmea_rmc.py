from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from gcu.app.models import Track, TrackFile, TrackMetadata, TrackPoint
from gcu.formats.base import FormatOptions
from gcu.formats.city_resolver import resolve_display_city
from gcu.formats.timezone_resolver import resolve_display_timezone


class NmeaRmcReader:
    format_id = "nmea-rmc"
    supported_sentence_types = {"GPRMC", "GNRMC"}

    def can_read(self, path: Path) -> bool:
        if path.suffix.lower() not in {".txt", ".nmea", ".log"}:
            return False
        try:
            with path.open("r", encoding="utf-8-sig", errors="ignore") as handle:
                for _ in range(20):
                    line = handle.readline()
                    if not line:
                        break
                    if self._looks_like_rmc(line.strip()):
                        return True
        except OSError:
            return False
        return False

    def read(self, path: Path, options: FormatOptions) -> TrackFile:
        warnings: list[str] = []
        points: list[TrackPoint] = []

        with path.open("r", encoding="utf-8-sig", errors="ignore") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    point = self._parse_rmc(line)
                except ValueError as exc:
                    warnings.append(f"line {line_number}: skipped invalid RMC sentence: {exc}")
                    continue
                if point is not None:
                    points.append(point)

        points.sort(key=lambda point: (point.timestamp_utc, point.latitude, point.longitude))
        if not points:
            raise ValueError(f"No valid RMC track points found in {path}")

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
            source_device="NMEA",
            display_timezone=display_timezone_name,
            display_city=display_city,
        )
        return TrackFile(
            source_path=path,
            source_format=self.format_id,
            track=Track(points=tuple(points), metadata=metadata),
            warnings=tuple(warnings),
        )

    def _looks_like_rmc(self, line: str) -> bool:
        if not line.startswith("$"):
            return False
        sentence_type = line[1:6]
        return sentence_type in self.supported_sentence_types

    def _parse_rmc(self, line: str) -> TrackPoint | None:
        self._verify_checksum(line)
        payload = line[1:] if line.startswith("$") else line
        payload = payload.split("*", 1)[0]
        fields = payload.split(",")
        if len(fields) < 10:
            raise ValueError("too few fields")
        if fields[0] not in self.supported_sentence_types:
            raise ValueError(f"unsupported sentence type {fields[0]}")
        if fields[2] != "A":
            return None

        timestamp_utc = self._parse_datetime(fields[1], fields[9])
        latitude = self._parse_coordinate(fields[3], fields[4], is_latitude=True)
        longitude = self._parse_coordinate(fields[5], fields[6], is_latitude=False)
        speed_mps = self._knots_to_mps(float(fields[7])) if fields[7] else None
        heading_deg = float(fields[8]) if fields[8] else None
        return TrackPoint(
            timestamp_utc=timestamp_utc,
            latitude=latitude,
            longitude=longitude,
            speed_mps=speed_mps,
            heading_deg=heading_deg,
        )

    def _parse_datetime(self, time_value: str, date_value: str) -> datetime:
        if len(time_value) < 6 or len(date_value) != 6:
            raise ValueError("RMC time/date must use HHMMSS(.sss) and DDMMYY")
        hour = int(time_value[0:2])
        minute = int(time_value[2:4])
        second = int(time_value[4:6])
        microsecond = 0
        if "." in time_value:
            fraction = time_value.split(".", 1)[1]
            microsecond = int((fraction + "000000")[:6])

        day = int(date_value[0:2])
        month = int(date_value[2:4])
        year_suffix = int(date_value[4:6])
        year = 1900 + year_suffix if year_suffix >= 80 else 2000 + year_suffix
        return datetime(year, month, day, hour, minute, second, microsecond, tzinfo=timezone.utc)

    def _parse_coordinate(self, value: str, hemisphere: str, is_latitude: bool) -> float:
        if not value or not hemisphere:
            raise ValueError("empty coordinate")
        degree_digits = 2 if is_latitude else 3
        degrees = int(value[:degree_digits])
        minutes = float(value[degree_digits:])
        decimal = degrees + (minutes / 60)
        if hemisphere.upper() in {"S", "W"}:
            return -decimal
        if hemisphere.upper() not in {"N", "E"}:
            raise ValueError(f"invalid hemisphere {hemisphere}")
        return decimal

    def _knots_to_mps(self, speed_knots: float) -> float:
        return speed_knots * 0.514444

    def _verify_checksum(self, line: str) -> None:
        if "*" not in line:
            return
        payload, checksum_text = line.split("*", 1)
        payload = payload[1:] if payload.startswith("$") else payload
        checksum_text = checksum_text[:2]
        if len(checksum_text) != 2:
            raise ValueError("invalid checksum")
        expected = 0
        for character in payload:
            expected ^= ord(character)
        try:
            actual = int(checksum_text, 16)
        except ValueError as exc:
            raise ValueError("invalid checksum") from exc
        if expected != actual:
            raise ValueError(f"checksum mismatch: expected {expected:02X}, got {actual:02X}")

    def _default_display_name(
        self,
        start_utc: datetime,
        duration_s: float,
        display_tz: ZoneInfo,
        city_name: str | None,
    ) -> str:
        local_dt = start_utc.astimezone(display_tz)
        total_minutes = max(0, int(duration_s // 60))
        hours = total_minutes // 60
        minutes = total_minutes % 60
        duration = f"{hours}h {minutes}m" if hours else f"{minutes}m"
        prefix = f"{city_name} " if city_name else ""
        return f"{prefix}GPS Track - {local_dt.strftime('%b %d')} {duration}"
