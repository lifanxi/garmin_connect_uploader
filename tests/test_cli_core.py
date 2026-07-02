from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from gcu.app.models import RemoteActivity, UploadResult
from gcu.app.models import TrackPoint
from gcu.app.precheck_service import PrecheckService
from gcu.app.sync_service import SyncOptions, SyncService
from gcu.cli.main import _existing_files
from gcu.duplicate.fingerprint import append_or_replace_token, fingerprint_track
from gcu.duplicate.matcher import MatchOptions, find_legacy_matches
from gcu.duplicate.remote_index import RemoteActivityIndex
from gcu.formats.base import FormatOptions
from gcu.formats.city_resolver import resolve_display_city
from gcu.formats.columbus_csv import ColumbusCsvReader
from gcu.formats.nmea_rmc import NmeaRmcReader
from gcu.formats.timezone_resolver import resolve_display_timezone
from gcu.garmin.client import _classify_upload_error
from gcu.garmin.errors import DuplicateUploadError, UploadConsentRequiredError
from gcu.garmin.signature import GCU_DEVICE_ID, GCU_MANUFACTURER


CSV_TEXT = """INDEX,TAG,DATE,TIME,LATITUDE N/S,LONGITUDE E/W,HEIGHT,SPEED,HEADING
2,T,251201,000002,30.0000020N,120.0000020E,2,7.2,90
1,T,251201,000001,30.0000010N,120.0000010E,1,3.6,80
"""

CSV_TEXT_SHORT = """INDEX,TAG,DATE,TIME,LATITUDE N/S,LONGITUDE E/W,HEIGHT,SPEED,HEADING
1,T,251201,010001,30.0000010N,120.0000010E,1,3.6,80
"""

NMEA_TEXT = """$GPRMC,011052.387,A,3203.7744,N,11848.7084,E,2.25,26.90,230308,,*37
$GPRMC,011056.387,A,3203.7677,N,11848.7043,E,0.81,162.57,230308,,*0F
"""

NMEA_US_TEXT = """$GPRMC,211903.326,A,3716.8900,N,12151.9508,W,60.71,160.62,050911,,*21
$GPRMC,211908.000,A,3716.8167,N,12151.9176,W,59.06,160.25,050911,,*20
"""


class CoreCliTests(unittest.TestCase):
    def test_columbus_reader_normalizes_and_sorts_points(self):
        path = self._write_csv(CSV_TEXT)
        track_file = ColumbusCsvReader().read(path, FormatOptions())

        self.assertEqual(track_file.track.metadata.point_count, 2)
        self.assertEqual(track_file.track.points[0].timestamp_utc.tzinfo, timezone.utc)
        self.assertEqual(track_file.track.points[0].timestamp_utc.hour, 0)
        self.assertEqual(track_file.track.points[0].latitude, 30.000001)
        self.assertAlmostEqual(track_file.track.points[0].speed_mps, 1.0)
        self.assertEqual(track_file.track.metadata.display_timezone, "Asia/Shanghai")
        self.assertEqual(track_file.track.metadata.display_city, "Hangzhou")
        self.assertEqual(track_file.track.metadata.display_name, "Hangzhou Track Me")

    def test_columbus_reader_can_override_source_timezone(self):
        path = self._write_csv(CSV_TEXT)
        track_file = ColumbusCsvReader().read(path, FormatOptions(timezone_name="Asia/Shanghai"))

        self.assertEqual(track_file.track.points[0].timestamp_utc.hour, 16)
        self.assertEqual(track_file.track.points[0].timestamp_utc.day, 30)

    def test_display_timezone_is_independent_from_source_timezone(self):
        text = """INDEX,TAG,DATE,TIME,LATITUDE N/S,LONGITUDE E/W,HEIGHT,SPEED,HEADING
1,T,251201,180001,30.0000010N,120.0000010E,1,3.6,80
"""
        path = self._write_csv(text)
        default_display = ColumbusCsvReader().read(path, FormatOptions())
        utc_display = ColumbusCsvReader().read(
            path,
            FormatOptions(display_timezone_name="UTC"),
        )

        self.assertEqual(default_display.track.metadata.start_time_utc.hour, 18)
        self.assertEqual(default_display.track.metadata.display_name, "Hangzhou Track Me")
        self.assertEqual(utc_display.track.metadata.display_name, "Hangzhou Track Me")

    def test_auto_display_timezone_uses_first_five_minute_majority(self):
        start = datetime(2025, 12, 1, 0, 0, 0, tzinfo=timezone.utc)
        points = (
            TrackPoint(start, 35.0, 139.0),
            TrackPoint(start + timedelta(minutes=1), 35.1, 139.1),
            TrackPoint(start + timedelta(minutes=2), 30.0, 120.0),
            TrackPoint(start + timedelta(minutes=6), 30.0, 120.0),
        )

        self.assertEqual(resolve_display_timezone(points, "auto"), "Asia/Tokyo")

    def test_auto_display_city_uses_middle_segment_majority(self):
        start = datetime(2025, 12, 1, 0, 0, 0, tzinfo=timezone.utc)
        points = (
            TrackPoint(start, 35.0, 139.0),
            TrackPoint(start + timedelta(minutes=30), 32.06, 118.81),
            TrackPoint(start + timedelta(minutes=31), 32.06, 118.81),
            TrackPoint(start + timedelta(minutes=60), 37.28, -121.86),
        )

        self.assertEqual(resolve_display_city(points, "auto"), "Nanjing")

    def test_nmea_rmc_reader_parses_utc_time_and_coordinates(self):
        path = self._write_file(NMEA_TEXT, "track.txt")
        track_file = NmeaRmcReader().read(path, FormatOptions())
        first = track_file.track.points[0]

        self.assertEqual(track_file.source_format, "nmea-rmc")
        self.assertEqual(first.timestamp_utc.isoformat(), "2008-03-23T01:10:52.387000+00:00")
        self.assertAlmostEqual(first.latitude, 32.0629066667)
        self.assertAlmostEqual(first.longitude, 118.8118066667)
        self.assertAlmostEqual(first.speed_mps, 2.25 * 0.514444)
        self.assertEqual(track_file.track.metadata.display_timezone, "Asia/Shanghai")
        self.assertEqual(track_file.track.metadata.display_city, "Nanjing")
        self.assertEqual(track_file.track.metadata.display_name, "Nanjing Track Me")

    def test_nmea_rmc_reader_detects_us_display_timezone(self):
        path = self._write_file(NMEA_US_TEXT, "track.txt")
        track_file = NmeaRmcReader().read(path, FormatOptions())
        first = track_file.track.points[0]

        self.assertEqual(first.timestamp_utc.isoformat(), "2011-09-05T21:19:03.326000+00:00")
        self.assertAlmostEqual(first.longitude, -121.8658466667)
        self.assertEqual(track_file.track.metadata.display_timezone, "America/Los_Angeles")
        self.assertEqual(track_file.track.metadata.display_city, "San Jose")
        self.assertEqual(track_file.track.metadata.display_name, "San Jose Track Me")

    def test_nmea_rmc_reader_rejects_bad_checksum(self):
        text = "$GPRMC,011052.387,A,3203.7744,N,11848.7084,E,2.25,26.90,230308,,*00\n"
        path = self._write_file(text, "bad.txt")

        with self.assertRaises(ValueError):
            NmeaRmcReader().read(path, FormatOptions())

    def test_precheck_reports_duplicate_overlap_and_conflict(self):
        duplicate_a = self._write_csv(CSV_TEXT, name="duplicate-a.CSV")
        duplicate_b = self._write_csv(CSV_TEXT, name="duplicate-b.CSV")
        conflict = self._write_csv(
            """INDEX,TAG,DATE,TIME,LATITUDE N/S,LONGITUDE E/W,HEIGHT,SPEED,HEADING
1,T,251201,000001,31.0000010N,121.0000010E,1,3.6,80
""",
            name="conflict.CSV",
        )

        report = PrecheckService().check(
            [duplicate_a, duplicate_b, conflict],
            SyncOptions(format_options=FormatOptions()),
        )

        self.assertEqual(report.checked_count, 3)
        self.assertEqual(len(report.duplicate_groups), 1)
        self.assertEqual(set(report.duplicate_groups[0].source_paths), {duplicate_a, duplicate_b})
        self.assertEqual(len(report.overlapping_points), 1)
        self.assertEqual(report.overlapping_points[0].count, 2)
        self.assertEqual(len(report.conflicting_points), 2)

    def test_cli_expands_globs_for_windows_shells(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        first = root / "a.CSV"
        second = root / "b.CSV"
        first.write_text(CSV_TEXT, encoding="utf-8")
        second.write_text(CSV_TEXT, encoding="utf-8")

        self.assertEqual(_existing_files([root / "*.CSV"]), [first, second])

    def test_fingerprint_is_stable_for_same_content(self):
        path = self._write_csv(CSV_TEXT)
        reader = ColumbusCsvReader()
        first = reader.read(path, FormatOptions())
        second = reader.read(path, FormatOptions())

        self.assertEqual(fingerprint_track(first.track), fingerprint_track(second.track))

    def test_remote_index_and_legacy_match(self):
        path = self._write_csv(CSV_TEXT)
        local_track = SyncService().inspect([path], SyncOptions(format_options=FormatOptions()))[0]
        metadata = local_track.track_file.track.metadata
        remote = RemoteActivity(
            activity_id=123,
            activity_name="Old GPS Track",
            begin_timestamp_ms=int(metadata.start_time_utc.timestamp() * 1000),
            start_latitude=metadata.start_latitude,
            start_longitude=metadata.start_longitude,
            duration_s=metadata.duration_s,
            manufacturer=GCU_MANUFACTURER,
            device_id=GCU_DEVICE_ID,
        )
        index = RemoteActivityIndex.build([remote])

        matches = find_legacy_matches(local_track, index, MatchOptions())
        self.assertEqual([item.activity_id for item in matches], [123])

    def test_append_or_replace_token(self):
        self.assertEqual(
            append_or_replace_token("Ride [gcu:v1:aaaaaaaaaaaaaaaa]", "[gcu:v1:bbbbbbbbbbbbbbbb]"),
            "Ride [gcu:v1:bbbbbbbbbbbbbbbb]",
        )

    def test_upload_conflict_resolves_unique_remote_match(self):
        path = self._write_csv(CSV_TEXT)
        local_track = SyncService().inspect([path], SyncOptions(format_options=FormatOptions()))[0]
        metadata = local_track.track_file.track.metadata
        remote = RemoteActivity(
            activity_id=456,
            activity_name="Existing activity",
            begin_timestamp_ms=int(metadata.start_time_utc.timestamp() * 1000),
            start_latitude=metadata.start_latitude,
            start_longitude=metadata.start_longitude,
            duration_s=metadata.duration_s,
            manufacturer=GCU_MANUFACTURER,
            device_id=GCU_DEVICE_ID,
        )
        garmin = ConflictGarmin(remote)

        decisions = SyncService().sync([path], garmin, SyncOptions(format_options=FormatOptions()))

        self.assertEqual(decisions[0].status, "upload-conflict")
        self.assertEqual(decisions[0].activity_id, 456)
        self.assertIn(local_track.token, garmin.updated_name)

    def test_backfill_refuses_to_modify_unsigned_remote_activity(self):
        path = self._write_csv(CSV_TEXT)
        local_track = SyncService().inspect([path], SyncOptions(format_options=FormatOptions()))[0]
        metadata = local_track.track_file.track.metadata
        remote = RemoteActivity(
            activity_id=654,
            activity_name="External activity",
            begin_timestamp_ms=int(metadata.start_time_utc.timestamp() * 1000),
            start_latitude=metadata.start_latitude,
            start_longitude=metadata.start_longitude,
            duration_s=metadata.duration_s,
            manufacturer="GARMIN",
            device_id=1,
        )
        garmin = ExistingActivityGarmin(remote)

        decisions = SyncService().backfill([path], garmin, SyncOptions(format_options=FormatOptions()))

        self.assertEqual(decisions[0].status, "failed")
        self.assertEqual(garmin.updated_name, "")
        self.assertIn("not signed as GCU upload", decisions[0].message)

    def test_dry_run_refuses_unsigned_legacy_match(self):
        path = self._write_csv(CSV_TEXT)
        local_track = SyncService().inspect([path], SyncOptions(format_options=FormatOptions()))[0]
        metadata = local_track.track_file.track.metadata
        remote = RemoteActivity(
            activity_id=655,
            activity_name="External activity",
            begin_timestamp_ms=int(metadata.start_time_utc.timestamp() * 1000),
            start_latitude=metadata.start_latitude,
            start_longitude=metadata.start_longitude,
            duration_s=metadata.duration_s,
            manufacturer="GARMIN",
            device_id=1,
        )
        garmin = ExistingActivityGarmin(remote)

        decisions = SyncService().sync([path], garmin, SyncOptions(format_options=FormatOptions(), dry_run=True))

        self.assertEqual(decisions[0].status, "failed")
        self.assertIn("not signed as GCU upload", decisions[0].message)

    def test_sync_uploads_larger_tracks_first(self):
        long_path = self._write_csv(CSV_TEXT, name="long.CSV")
        short_path = self._write_csv(CSV_TEXT_SHORT, name="short.CSV")
        service = SyncService()
        options = SyncOptions(format_options=FormatOptions())
        long_name = service.inspect([long_path], options)[0].planned_name
        garmin = ImmediateUploadGarmin()

        service.sync([short_path, long_path], garmin, options)

        self.assertEqual(garmin.updated_names[0], long_name)

    def test_upload_with_activity_id_refuses_unsigned_remote_activity(self):
        path = self._write_csv(CSV_TEXT)
        garmin = ImmediateUploadGarmin(signed=False)

        decisions = SyncService().sync([path], garmin, SyncOptions(format_options=FormatOptions()))

        self.assertEqual(decisions[0].status, "failed")
        self.assertEqual(garmin.updated_names, [])
        self.assertIn("not signed as GCU upload", decisions[0].message)

    def test_sync_skips_duplicate_local_track_in_same_batch(self):
        first = self._write_csv(CSV_TEXT, name="first.CSV")
        second = self._write_csv(CSV_TEXT, name="second.CSV")
        garmin = ImmediateUploadGarmin()

        decisions = SyncService().sync([first, second], garmin, SyncOptions(format_options=FormatOptions()))

        self.assertEqual(garmin.upload_count, 1)
        self.assertEqual([decision.status for decision in decisions], ["upload", "skip-token"])
        self.assertEqual(decisions[1].message, "duplicate local track in same batch")

    def test_unsigned_remote_token_does_not_skip_upload(self):
        path = self._write_csv(CSV_TEXT)
        local_track = SyncService().inspect([path], SyncOptions(format_options=FormatOptions()))[0]
        remote = RemoteActivity(
            activity_id=777,
            activity_name=f"External activity {local_track.token}",
            manufacturer="GARMIN",
            device_id=1,
        )
        garmin = ExistingActivityGarmin(remote)

        decisions = SyncService().sync([path], garmin, SyncOptions(format_options=FormatOptions(), dry_run=True))

        self.assertEqual(decisions[0].status, "upload")

    def test_estimated_post_upload_wait_scales_with_point_count(self):
        path = self._write_csv(CSV_TEXT)
        service = SyncService()
        options = SyncOptions(
            format_options=FormatOptions(),
            post_upload_wait_base_s=30,
            post_upload_wait_per_1000_points_s=5,
            post_upload_max_wait_s=32,
        )
        local_track = service.inspect([path], options)[0]

        self.assertEqual(service._estimated_post_upload_wait_s(local_track, options), 32)

    def test_upload_without_activity_id_is_tagged_by_background_lookup(self):
        path = self._write_csv(CSV_TEXT)
        service = SyncService()
        options = SyncOptions(format_options=FormatOptions(), post_upload_tag_workers=2)
        local_track = service.inspect([path], options)[0]
        metadata = local_track.track_file.track.metadata
        remote = RemoteActivity(
            activity_id=789,
            activity_name="New remote activity",
            begin_timestamp_ms=int(metadata.start_time_utc.timestamp() * 1000),
            start_latitude=metadata.start_latitude,
            start_longitude=metadata.start_longitude,
            duration_s=metadata.duration_s,
            manufacturer=GCU_MANUFACTURER,
            device_id=GCU_DEVICE_ID,
        )
        garmin = DeferredLookupGarmin(remote)

        decisions = service.sync([path], garmin, options)

        self.assertEqual(decisions[0].activity_id, 789)
        self.assertIn(local_track.token, garmin.updated_name)

    def test_purge_deletes_only_signed_gcu_activities(self):
        signed = RemoteActivity(
            activity_id=901,
            activity_name="GCU activity",
            manufacturer=GCU_MANUFACTURER,
            device_id=GCU_DEVICE_ID,
        )
        unsigned = RemoteActivity(
            activity_id=902,
            activity_name="External activity",
            manufacturer="GARMIN",
            device_id=1,
        )
        garmin = PurgeGarmin([signed, unsigned])

        summary = SyncService().purge(
            garmin,
            start_date=date(1970, 1, 1),
            end_date=date(2026, 7, 1),
            dry_run=False,
        )

        self.assertEqual(garmin.deleted_ids, [901])
        self.assertEqual(summary.scanned_count, 2)
        self.assertEqual(summary.matched_count, 1)
        self.assertEqual(summary.deleted_count, 1)
        self.assertEqual(summary.skipped_unsigned_count, 1)

    def test_upload_consent_error_is_classified(self):
        response = FakeResponse(
            412,
            {
                "detailedImportResult": {
                    "failures": [
                        {
                            "messages": [
                                {
                                    "content": "The user is from EU location, but upload consent is not yet granted or revoked"
                                }
                            ]
                        }
                    ]
                }
            },
        )

        self.assertIsInstance(_classify_upload_error(response), UploadConsentRequiredError)

    def _write_csv(self, text: str, name: str = "track.CSV") -> Path:
        return self._write_file(text, name)

    def _write_file(self, text: str, name: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / name
        path.write_text(text, encoding="utf-8")
        return path

class ConflictGarmin:
    def __init__(self, remote: RemoteActivity):
        self.remote = remote
        self.updated_name = ""
        self.list_calls = 0

    def list_activities(self, start_date, end_date):
        self.list_calls += 1
        if self.list_calls == 1:
            return []
        return [self.remote]

    def upload_activity(self, file_path):
        raise DuplicateUploadError("duplicate")

    def update_activity_name(self, activity_id: int, activity_name: str):
        self.updated_name = activity_name


class ExistingActivityGarmin:
    def __init__(self, remote: RemoteActivity):
        self.remote = remote
        self.updated_name = ""

    def list_activities(self, start_date, end_date):
        return [self.remote]

    def upload_activity(self, file_path):
        return UploadResult(activity_id=None)

    def update_activity_name(self, activity_id: int, activity_name: str):
        self.updated_name = activity_name


class ImmediateUploadGarmin:
    def __init__(self, signed: bool = True):
        self.signed = signed
        self.next_id = 1000
        self.updated_names: list[str] = []
        self.activities: list[RemoteActivity] = []
        self.upload_count = 0

    def list_activities(self, start_date, end_date):
        return self.activities

    def upload_activity(self, file_path):
        self.upload_count += 1
        self.next_id += 1
        self.activities.append(
            RemoteActivity(
                activity_id=self.next_id,
                activity_name="Uploaded activity",
                manufacturer=GCU_MANUFACTURER if self.signed else "GARMIN",
                device_id=GCU_DEVICE_ID if self.signed else 1,
            )
        )
        return UploadResult(activity_id=self.next_id)

    def update_activity_name(self, activity_id: int, activity_name: str):
        self.updated_names.append(activity_name)


class DeferredLookupGarmin:
    def __init__(self, remote: RemoteActivity):
        self.remote = remote
        self.updated_name = ""

    def list_activities(self, start_date, end_date):
        return []

    def upload_activity(self, file_path):
        return UploadResult(activity_id=None)

    def wait_for_activity_match(self, start_date, predicate, max_wait_s=30):
        return self.remote if predicate(self.remote) else None

    def update_activity_name(self, activity_id: int, activity_name: str):
        self.updated_name = activity_name


class PurgeGarmin:
    def __init__(self, activities: list[RemoteActivity]):
        self.activities = activities
        self.deleted_ids: list[int] = []

    def list_activities(self, start_date, end_date):
        return self.activities

    def upload_activity(self, file_path):
        return UploadResult(activity_id=None)

    def update_activity_name(self, activity_id: int, activity_name: str):
        raise AssertionError("purge must not update activity names")

    def delete_activity(self, activity_id: int):
        self.deleted_ids.append(activity_id)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


if __name__ == "__main__":
    unittest.main()
