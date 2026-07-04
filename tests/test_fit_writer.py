from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fit_tool.fit_file import FitFile
from fit_tool.profile.messages.record_message import RecordMessage

from gcu.app.models import Track, TrackMetadata, TrackPoint
from gcu.export.fit_writer import write_fit


def _build_track(*altitudes: float | None) -> Track:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    points = []
    for index, altitude in enumerate(altitudes):
        points.append(
            TrackPoint(
                timestamp_utc=start + timedelta(seconds=index),
                latitude=30.0 + index * 0.0001,
                longitude=120.0 + index * 0.0001,
                altitude_m=altitude,
                speed_mps=2.0,
            )
        )
    metadata = TrackMetadata(
        start_time_utc=start,
        end_time_utc=points[-1].timestamp_utc,
        duration_s=(len(points) - 1),
        point_count=len(points),
        start_latitude=points[0].latitude,
        start_longitude=points[0].longitude,
        end_latitude=points[-1].latitude,
        end_longitude=points[-1].longitude,
        display_name="Test Track",
    )
    return Track(points=tuple(points), metadata=metadata)


def _extract_record_altitudes(path: Path) -> list[tuple[float | None, float | None]]:
    fit_file = FitFile.from_file(str(path))
    records = []
    for rec in fit_file.records:
        msg = rec.message
        if isinstance(msg, RecordMessage):
            records.append((msg.altitude, msg.enhanced_altitude))
    return records


class FitWriterTests(unittest.TestCase):
    def test_fit_writer_skips_extreme_negative_altitudes(self):
        track = _build_track(-55990.0, -600.0, -500.0, -100.0, 10.0, 13000.0)
        with TemporaryDirectory() as directory:
            output = Path(directory) / "track.fit"

            write_fit(track, output)

            records = _extract_record_altitudes(output)
            self.assertEqual(records[0], (None, None))
            self.assertEqual(records[1], (None, None))
            self.assertEqual(records[2], (-500.0, -500.0))
            self.assertEqual(records[3], (-100.0, -100.0))
            self.assertEqual(records[4], (10.0, 10.0))
            self.assertEqual(records[5], (None, 13000.0))
