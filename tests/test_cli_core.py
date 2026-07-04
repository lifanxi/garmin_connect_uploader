from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from requests import Response
from requests import Session
from requests import Request

from gcu.app.models import FileCheckError, PurgeDecision, PurgeSummary, RemoteActivity, SyncDecision, UploadResult
from gcu.app.models import Track
from gcu.app.models import TrackMetadata
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
from gcu.formats.fit import FitReader
from gcu.formats.gpx import GpxReader
from gcu.formats.nmea_rmc import NmeaRmcReader
from gcu.formats.timezone_resolver import resolve_display_timezone
from gcu.export.fit_writer import write_fit
from gcu.garmin.client import ACCOUNT_HINT_FILE
from gcu.garmin.client import _classify_upload_error
from gcu.garmin.client import _extract_email
from gcu.garmin.client import GARMIN_WEB_USER_AGENT, GarminClient
from gcu.garmin.errors import DuplicateUploadError, UploadConsentRequiredError
from gcu.garmin.signature import GCU_DEVICE_ID, GCU_MANUFACTURER
from gcu.garmin.verbose_http import configure_verbose_http_logging
import gcu.gui.main as gui_main
from gcu.gui.main import MainWindow
from gcu.gui.main import (
    FILE_COLUMN,
    PLAN_COLUMN,
    PLAN_STATUS_ROLE,
    POINTS_COLUMN,
    SortableTableItem,
    TRANSLATIONS,
    _decision_display_name,
    _format_decision_summary,
    _format_purge,
    _friendly_error_message,
    _localized_decision_message,
    _localized_plan_status,
    _load_saved_domain,
    _normalize_domain,
    _numeric_sort_value,
    _save_saved_domain,
)


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

GPX_TEXT = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="Unit Test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk>
    <name>sample</name>
    <trkseg>
      <trkpt lat="30.2781937" lon="120.1404531">
        <ele>54</ele>
        <time>2026-03-19T23:54:27Z</time>
      </trkpt>
      <trkpt lat="30.2781823" lon="120.1404645">
        <ele>55</ele>
        <time>2026-03-19T23:54:28+00:00</time>
        <extensions>
          <speed>1.25</speed>
          <course>90</course>
        </extensions>
      </trkpt>
    </trkseg>
  </trk>
</gpx>
"""


class CoreCliTests(unittest.TestCase):
    def test_gui_persists_session_domain(self):
        with tempfile.TemporaryDirectory() as directory:
            session_dir = Path(directory)

            self.assertIsNone(_load_saved_domain(session_dir))
            (session_dir / ACCOUNT_HINT_FILE).write_text('{"login_username": "person@example.com"}', encoding="utf-8")
            _save_saved_domain("garmin.com", session_dir)
            self.assertEqual(_load_saved_domain(session_dir), "garmin.com")
            self.assertEqual(
                (session_dir / ACCOUNT_HINT_FILE).read_text(encoding="utf-8"),
                '{\n  "domain": "garmin.com",\n  "login_username": "person@example.com"\n}',
            )

            _save_saved_domain("garmin.cn", session_dir)
            self.assertEqual(_load_saved_domain(session_dir), "garmin.cn")

            self.assertEqual(_normalize_domain("garmin.com"), "garmin.com")
            self.assertIsNone(_normalize_domain("example.com"))

    def test_gui_reads_legacy_session_domain_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            session_dir = Path(directory)
            session_dir.mkdir(exist_ok=True)
            (session_dir / "gcu_session.json").write_text('{"domain": "garmin.com"}', encoding="utf-8")

            self.assertEqual(_load_saved_domain(session_dir), "garmin.com")

            _save_saved_domain("garmin.cn", session_dir)
            self.assertFalse((session_dir / "gcu_session.json").exists())
            self.assertEqual(_load_saved_domain(session_dir), "garmin.cn")

    def test_gui_ignores_invalid_session_domain_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            session_dir = Path(directory)
            session_dir.mkdir(exist_ok=True)

            (session_dir / ACCOUNT_HINT_FILE).write_text("{bad json", encoding="utf-8")
            self.assertIsNone(_load_saved_domain(session_dir))

            (session_dir / ACCOUNT_HINT_FILE).write_text('{"domain": "example.com"}', encoding="utf-8")
            self.assertIsNone(_load_saved_domain(session_dir))

    def test_gui_error_summary_keeps_traceback_as_detail(self):
        def tr(key):
            return {
                "error_login_summary": "Login failed.",
                "error_auth_summary": "Auth failed.",
                "error_network_summary": "Network failed.",
                "error_server_summary": "Server failed.",
                "error_default_summary": "Operation failed.",
            }[key]

        message = 'Traceback...\ngarth.exc.GarthException: SSO error: FAILURE'

        self.assertEqual(
            _friendly_error_message(message, tr, context="login"),
            "Login failed.\n\ngarth.exc.GarthException: SSO error: FAILURE",
        )
        self.assertEqual(
            _friendly_error_message("requests.exceptions.Timeout: timed out", tr),
            "Network failed.\n\nrequests.exceptions.Timeout: timed out",
        )

    def test_gui_has_friendly_missing_session_message(self):
        self.assertIn("无有效登录凭证", TRANSLATIONS["zh"]["no_valid_session"])
        self.assertIn("No valid login session", TRANSLATIONS["en"]["no_valid_session"])

    def test_gui_localizes_plan_status_and_summary(self):
        tr = lambda key, **kwargs: TRANSLATIONS["zh"][key].format(**kwargs) if kwargs else TRANSLATIONS["zh"][key]
        decisions = [
            SyncDecision(source_path=Path("a.CSV"), status="upload", token="a", planned_name="a"),
            SyncDecision(source_path=Path("b.CSV"), status="skip-token", token="b", planned_name="b"),
            SyncDecision(source_path=Path("c.CSV"), status="skip-legacy-match", token="c", planned_name="c"),
            SyncDecision(source_path=Path("d.CSV"), status="failed", token="d", planned_name="d"),
            SyncDecision(source_path=Path("e.CSV"), status="ambiguous", token="e", planned_name="e"),
        ]

        self.assertEqual(_localized_plan_status("upload", tr), "上传")
        self.assertEqual(_localized_plan_status("skip-token", tr), "跳过")
        self.assertEqual(_localized_plan_status("skip-legacy-match", tr), "补填Token")
        self.assertEqual(_localized_plan_status("ambiguous", tr), "需人工确认")
        self.assertEqual(
            _format_decision_summary(decisions, tr, plan=True),
            "已完成 5 个轨迹任务计划，其中上传 1 条，跳过 1 条，补填Token 1 条，失败 1 条，其它 1 条",
        )

    def test_gui_shows_remote_name_when_decision_has_remote_match(self):
        remote = RemoteActivity(activity_id=1, activity_name="Current remote name")
        decision = SyncDecision(
            source_path=Path("a.CSV"),
            status="skip-legacy-match",
            token="[gcu:v1:aaaaaaaaaaaaaaaa]",
            planned_name="Current remote name [gcu:v1:aaaaaaaaaaaaaaaa]",
            candidates=(remote,),
        )

        self.assertEqual(_decision_display_name(decision), "Current remote name")

    def test_gui_shows_planned_name_when_decision_has_no_remote_match(self):
        decision = SyncDecision(
            source_path=Path("a.CSV"),
            status="upload",
            token="[gcu:v1:aaaaaaaaaaaaaaaa]",
            planned_name="Local planned name [gcu:v1:aaaaaaaaaaaaaaaa]",
        )

        self.assertEqual(_decision_display_name(decision), "Local planned name [gcu:v1:aaaaaaaaaaaaaaaa]")

    def test_gui_localizes_known_decision_messages(self):
        tr = lambda key, **kwargs: TRANSLATIONS["zh"][key].format(**kwargs) if kwargs else TRANSLATIONS["zh"][key]

        self.assertEqual(_localized_decision_message("uploaded and tagged", tr), "已上传并完成标记")
        self.assertEqual(
            _localized_decision_message("uploaded; activity unavailable after 35s, not tagged", tr),
            "已上传，但 35 秒后仍无法获取 Garmin 活动，未完成标记",
        )
        self.assertEqual(
            _localized_decision_message("ValueError: bad data", tr),
            "ValueError: bad data",
        )

    def test_gui_purge_preview_log_can_omit_details(self):
        tr = lambda key, **kwargs: TRANSLATIONS["en"][key].format(**kwargs) if kwargs else TRANSLATIONS["en"][key]
        summary = PurgeSummary(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            scanned_count=2,
            matched_count=1,
            deleted_count=0,
            skipped_unsigned_count=1,
            dry_run=True,
            decisions=(
                PurgeDecision(
                    activity_id=123,
                    activity_name="Hangzhou Track Me",
                    status="would-delete",
                    manufacturer="HOLUX",
                    device_id=0x12345678,
                ),
            ),
        )

        preview = _format_purge(summary, tr, include_details=False)
        detail = _format_purge(summary, tr)

        self.assertIn("would delete: 1", preview)
        self.assertNotIn("would-delete activity=123", preview)
        self.assertIn("would-delete activity=123", detail)

    def test_gui_uses_current_table_order_for_check_and_run(self):
        harness = FakeMainWindowHarness()
        harness.files_table = FakeTable(
            [
                {FILE_COLUMN: FakeItem("/tmp/second.CSV"), PLAN_COLUMN: FakeItem(status="skip-token")},
                {FILE_COLUMN: FakeItem("/tmp/first.CSV"), PLAN_COLUMN: FakeItem(status="upload")},
                {FILE_COLUMN: FakeItem("/tmp/third.CSV"), PLAN_COLUMN: FakeItem(status="skip-legacy-match")},
            ]
        )

        self.assertEqual(
            MainWindow._table_files(harness),
            [Path("/tmp/second.CSV"), Path("/tmp/first.CSV"), Path("/tmp/third.CSV")],
        )
        self.assertEqual(
            MainWindow._runnable_plan_files(harness),
            [Path("/tmp/first.CSV"), Path("/tmp/third.CSV")],
        )

    def test_gui_points_column_sorts_as_number(self):
        small = SortableTableItem("20")
        large = SortableTableItem("100")
        small.setData(gui_main.Qt.UserRole + 2, _numeric_sort_value(POINTS_COLUMN, "20"))
        large.setData(gui_main.Qt.UserRole + 2, _numeric_sort_value(POINTS_COLUMN, "100"))

        self.assertLess(small, large)

    def test_gui_sortable_table_item_handles_empty_numeric_cells(self):
        empty = SortableTableItem("")
        number = SortableTableItem("20")
        empty.setData(gui_main.Qt.UserRole + 2, _numeric_sort_value(POINTS_COLUMN, ""))
        number.setData(gui_main.Qt.UserRole + 2, _numeric_sort_value(POINTS_COLUMN, "20"))

        self.assertLess(number, empty)
        self.assertFalse(empty < number)

    def test_gui_sortable_table_item_compares_text_without_qt_fallback(self):
        first = SortableTableItem("a-file.csv")
        second = SortableTableItem("b-file.csv")

        self.assertLess(first, second)

    def test_garmin_client_overrides_garth_user_agent(self):
        client = GarminClient(domain="garmin.cn")

        self.assertEqual(client.client.sess.headers["User-Agent"], GARMIN_WEB_USER_AGENT)
        self.assertEqual(client.garth.sso.SSO_PAGE_HEADERS["User-Agent"], GARMIN_WEB_USER_AGENT)

    def test_garmin_client_uses_isolated_garth_client(self):
        first = GarminClient(domain="garmin.cn")
        second = GarminClient(domain="garmin.cn")

        self.assertIsNot(first.client, second.client)
        self.assertIsNot(first.client, first.garth.client)

    def test_verbose_http_logging_records_request_and_response(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        log_path = Path(directory.name) / "http.log"
        session = Session()

        with patch.dict(
            "os.environ",
            {
                "GCU_GARMIN_VERBOSE_HTTP": "1",
                "GCU_GARMIN_VERBOSE_HTTP_LOG": str(log_path),
            },
        ):
            configure_verbose_http_logging(session)

        request = Request(
            "POST",
            "https://connectapi.garmin.cn/upload-service/upload",
            headers={"Authorization": "Bearer token"},
            data=b"body",
        )
        prepared = session.prepare_request(request)
        response = Response()
        response.status_code = 200
        response.reason = "OK"
        response.url = prepared.url
        response.request = prepared
        response._content = b'{"activityId":123}'
        response.headers["Content-Type"] = "application/json"

        for hook in session.hooks["response"]:
            hook(response)

        text = log_path.read_text(encoding="utf-8")
        self.assertIn("POST https://connectapi.garmin.cn/upload-service/upload", text)
        self.assertIn("Authorization: Bearer token", text)
        self.assertIn("body", text)
        self.assertIn('{"activityId":123}', text)

    def test_verbose_http_logging_failure_does_not_break_response_hook(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        session = Session()

        with patch.dict(
            "os.environ",
            {
                "GCU_GARMIN_VERBOSE_HTTP": "1",
                "GCU_GARMIN_VERBOSE_HTTP_LOG": directory.name,
            },
        ):
            configure_verbose_http_logging(session)

        request = Request("GET", "https://connectapi.garmin.cn/test")
        prepared = session.prepare_request(request)
        response = Response()
        response.status_code = 200
        response.reason = "OK"
        response.url = prepared.url
        response.request = prepared
        response._content = b"ok"

        for hook in session.hooks["response"]:
            self.assertIs(hook(response), response)

    def test_current_user_prefers_saved_email_hint_over_profile_phone(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        client = GarminClient(domain="garmin.cn", session_dir=Path(directory.name))
        client._save_account_hint("person@example.com")

        class ProfileClient:
            def connectapi(self, path):
                return {
                    "userName": "13800138000",
                    "displayName": "Display",
                    "fullName": "Full Name",
                    "profileId": 123,
                }

        client.client = ProfileClient()
        user = client.current_user()

        self.assertEqual(user.username, "13800138000")
        self.assertEqual(user.email, "person@example.com")

    def test_account_hint_preserves_saved_domain(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        session_dir = Path(directory.name)
        session_dir.mkdir(exist_ok=True)
        (session_dir / ACCOUNT_HINT_FILE).write_text('{"domain": "garmin.com"}', encoding="utf-8")
        client = GarminClient(domain="garmin.com", session_dir=session_dir)

        client._save_account_hint("person@example.com")

        self.assertEqual(
            json.loads((session_dir / ACCOUNT_HINT_FILE).read_text(encoding="utf-8")),
            {"domain": "garmin.com", "login_username": "person@example.com"},
        )

    def test_extract_email_accepts_common_profile_keys(self):
        self.assertEqual(_extract_email({"emailAddress": "person@example.com"}), "person@example.com")
        self.assertEqual(_extract_email({"userName": "13800138000"}), "")

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

    def test_auto_display_city_uses_progressive_segment_majority(self):
        start = datetime(2025, 12, 1, 0, 0, 0, tzinfo=timezone.utc)
        points = (
            TrackPoint(start, 35.6895, 139.6917),
            TrackPoint(start + timedelta(minutes=15), 32.06, 118.81),
            TrackPoint(start + timedelta(minutes=30), 32.06, 118.81),
            TrackPoint(start + timedelta(minutes=45), 32.06, 118.81),
            TrackPoint(start + timedelta(minutes=60), 37.28, -121.86),
        )

        self.assertEqual(resolve_display_city(points, "auto"), "Nanjing")

    def test_auto_display_city_uses_start_city_when_end_matches_start(self):
        start = datetime(2025, 12, 1, 0, 0, 0, tzinfo=timezone.utc)
        points = (
            TrackPoint(start, 35.6895, 139.6917),
            TrackPoint(start + timedelta(minutes=30), 32.06, 118.81),
            TrackPoint(start + timedelta(minutes=60), 35.6895, 139.6917),
        )

        self.assertEqual(resolve_display_city(points, "auto"), "Tokyo")

    def test_auto_display_city_falls_back_to_start_when_no_city_wins(self):
        start = datetime(2025, 12, 1, 0, 0, 0, tzinfo=timezone.utc)
        points = (
            TrackPoint(start, 35.6895, 139.6917),
            TrackPoint(start + timedelta(minutes=30), 32.06, 118.81),
            TrackPoint(start + timedelta(minutes=60), 37.28, -121.86),
        )

        self.assertEqual(resolve_display_city(points, "auto"), "Tokyo")

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

    def test_gpx_reader_parses_track_points(self):
        path = self._write_file(GPX_TEXT, "track.gpx")

        track_file = GpxReader().read(path, FormatOptions(display_city_name="Hangzhou"))
        points = track_file.track.points

        self.assertEqual(track_file.source_format, "gpx")
        self.assertEqual(track_file.track.metadata.source_device, "Unit Test")
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0].timestamp_utc, datetime(2026, 3, 19, 23, 54, 27, tzinfo=timezone.utc))
        self.assertEqual(points[0].altitude_m, 54)
        self.assertEqual(points[1].speed_mps, 1.25)
        self.assertEqual(points[1].heading_deg, 90)
        self.assertEqual(track_file.track.metadata.display_name, "Hangzhou Track Me")

    def test_gpx_reader_accepts_sample_files(self):
        path = self._write_file(
            """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="Columbus GPS - http://cbgps.com/" xmlns="http://www.topografix.com/GPX/1/1">
  <trk>
    <name>sample</name>
    <trkseg>
      <trkpt lat="30.2781937" lon="120.1404531"><ele>54</ele><time>2026-03-19T23:54:29Z</time></trkpt>
      <trkpt lat="30.2781823" lon="120.1404645"><ele>54</ele><time>2026-03-19T23:54:27Z</time></trkpt>
    </trkseg>
    <trkseg>
      <trkpt lat="30.2781753" lon="120.1404522"><ele>53</ele><time>2026-03-19T23:54:28Z</time></trkpt>
    </trkseg>
  </trk>
</gpx>
""",
            "sample.GPX",
        )

        reader = GpxReader()
        self.assertTrue(reader.can_read(path))

        track_file = reader.read(path, FormatOptions(display_city_name="Hangzhou"))

        self.assertEqual(track_file.source_format, "gpx")
        self.assertEqual(track_file.track.metadata.source_device, "Columbus GPS - http://cbgps.com/")
        self.assertEqual(track_file.track.metadata.point_count, 3)
        self.assertEqual(track_file.track.metadata.display_timezone, "Asia/Shanghai")
        self.assertEqual(
            [point.timestamp_utc.second for point in track_file.track.points],
            [27, 28, 29],
        )

    def test_format_auto_detects_gpx(self):
        path = self._write_file(GPX_TEXT, "auto.gpx")

        local_track = SyncService().inspect(
            [path],
            SyncOptions(format_options=FormatOptions(display_city_name="Hangzhou")),
        )[0]

        self.assertEqual(local_track.track_file.source_format, "gpx")

    def test_fit_reader_parses_written_fit(self):
        csv_path = self._write_csv(CSV_TEXT)
        local_track = SyncService().inspect(
            [csv_path],
            SyncOptions(format_options=FormatOptions(display_city_name="Hangzhou")),
        )[0]
        fit_path = csv_path.with_suffix(".fit")
        write_fit(local_track.track_file.track, fit_path, local_track.planned_name)

        reader = FitReader()
        self.assertTrue(reader.can_read(fit_path))
        track_file = reader.read(fit_path, FormatOptions(display_city_name="Hangzhou"))
        points = track_file.track.points

        self.assertEqual(track_file.source_format, "fit")
        self.assertIn("manufacturer=", track_file.track.metadata.source_device or "")
        self.assertEqual(track_file.track.metadata.point_count, 2)
        self.assertEqual(points[0].timestamp_utc, datetime(2025, 12, 1, 0, 0, 1, tzinfo=timezone.utc))
        self.assertAlmostEqual(points[0].latitude, 30.000001, places=5)
        self.assertAlmostEqual(points[0].longitude, 120.000001, places=5)
        self.assertEqual(points[0].altitude_m, 1)
        self.assertEqual(points[0].speed_mps, 1)
        self.assertEqual(track_file.track.metadata.display_name, "Hangzhou Track Me")

    def test_fit_reader_uses_activity_start_metadata_when_records_start_later(self):
        csv_path = self._write_csv(CSV_TEXT)
        local_track = SyncService().inspect(
            [csv_path],
            SyncOptions(format_options=FormatOptions(display_city_name="Hangzhou")),
        )[0]
        original_track = local_track.track_file.track
        activity_start = datetime(2025, 11, 30, 22, 38, 39, tzinfo=timezone.utc)
        track = Track(
            points=original_track.points,
            metadata=TrackMetadata(
                start_time_utc=activity_start,
                end_time_utc=original_track.metadata.end_time_utc,
                duration_s=(original_track.metadata.end_time_utc - activity_start).total_seconds(),
                point_count=original_track.metadata.point_count,
                start_latitude=original_track.metadata.start_latitude,
                start_longitude=original_track.metadata.start_longitude,
                end_latitude=original_track.metadata.end_latitude,
                end_longitude=original_track.metadata.end_longitude,
                display_name=original_track.metadata.display_name,
                source_device=original_track.metadata.source_device,
                display_timezone=original_track.metadata.display_timezone,
                display_city=original_track.metadata.display_city,
            ),
        )
        fit_path = csv_path.with_suffix(".fit")
        write_fit(track, fit_path, local_track.planned_name)

        track_file = FitReader().read(fit_path, FormatOptions(display_city_name="Hangzhou"))

        self.assertEqual(track_file.track.points[0].timestamp_utc, original_track.points[0].timestamp_utc)
        self.assertEqual(track_file.track.metadata.start_time_utc, activity_start)
        self.assertEqual(track_file.track.metadata.end_time_utc, original_track.metadata.end_time_utc)

    def test_fit_reader_uses_fit_timer_duration_metadata(self):
        csv_path = self._write_csv(CSV_TEXT)
        local_track = SyncService().inspect(
            [csv_path],
            SyncOptions(format_options=FormatOptions(display_city_name="Hangzhou")),
        )[0]
        original_track = local_track.track_file.track
        timer_duration_s = 40202.09765625
        track = Track(
            points=original_track.points,
            metadata=TrackMetadata(
                start_time_utc=original_track.metadata.start_time_utc,
                end_time_utc=original_track.metadata.end_time_utc,
                duration_s=timer_duration_s,
                point_count=original_track.metadata.point_count,
                start_latitude=original_track.metadata.start_latitude,
                start_longitude=original_track.metadata.start_longitude,
                end_latitude=original_track.metadata.end_latitude,
                end_longitude=original_track.metadata.end_longitude,
                display_name=original_track.metadata.display_name,
                source_device=original_track.metadata.source_device,
                display_timezone=original_track.metadata.display_timezone,
                display_city=original_track.metadata.display_city,
            ),
        )
        fit_path = csv_path.with_suffix(".fit")
        write_fit(track, fit_path, local_track.planned_name)

        track_file = FitReader().read(fit_path, FormatOptions(display_city_name="Hangzhou"))

        self.assertAlmostEqual(track_file.track.metadata.duration_s, timer_duration_s, places=3)
        self.assertAlmostEqual(
            track_file.track.metadata.end_time_utc.timestamp(),
            (original_track.metadata.start_time_utc + timedelta(seconds=timer_duration_s)).timestamp(),
            places=3,
        )

    def test_format_auto_detects_fit(self):
        csv_path = self._write_csv(CSV_TEXT)
        local_track = SyncService().inspect(
            [csv_path],
            SyncOptions(format_options=FormatOptions(display_city_name="Hangzhou")),
        )[0]
        fit_path = csv_path.with_suffix(".FIT")
        write_fit(local_track.track_file.track, fit_path, local_track.planned_name)

        inspected = SyncService().inspect(
            [fit_path],
            SyncOptions(format_options=FormatOptions(display_city_name="Hangzhou")),
        )[0]

        self.assertEqual(inspected.track_file.source_format, "fit")

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

    def test_precheck_reports_file_progress(self):
        first = self._write_csv(CSV_TEXT, name="progress-a.CSV")
        second = self._write_csv(CSV_TEXT_SHORT, name="progress-b.CSV")
        seen = []

        PrecheckService().check(
            [first, second],
            SyncOptions(format_options=FormatOptions()),
            on_file=lambda index, count, path: seen.append((index, count, path.name)),
        )

        self.assertEqual(seen, [(1, 2, "progress-a.CSV"), (2, 2, "progress-b.CSV")])

    def test_precheck_reports_parsed_tracks_for_reuse(self):
        first = self._write_csv(CSV_TEXT, name="reuse-a.CSV")
        second = self._write_csv(CSV_TEXT_SHORT, name="reuse-b.CSV")
        tracks = []

        report = PrecheckService().check(
            [first, second],
            SyncOptions(format_options=FormatOptions()),
            on_track=tracks.append,
        )

        self.assertEqual(report.checked_count, 2)
        self.assertEqual([track.track_file.source_path.name for track in tracks], ["reuse-a.CSV", "reuse-b.CSV"])

    def test_precheck_records_file_errors_without_stopping(self):
        good = self._write_csv(CSV_TEXT, name="good.CSV")
        bad = self._write_file("not a supported track\n", "bad.txt")

        report = PrecheckService().check(
            [bad, good],
            SyncOptions(format_options=FormatOptions()),
        )

        self.assertEqual(report.checked_count, 1)
        self.assertEqual(len(report.file_errors), 1)
        self.assertEqual(report.file_errors[0].source_path, bad)
        self.assertIn("Could not detect track format", report.file_errors[0].message)

    def test_precheck_check_tracks_does_not_reinspect(self):
        options = SyncOptions(format_options=FormatOptions())
        duplicate_a = self._write_csv(CSV_TEXT, name="reuse-a.CSV")
        duplicate_b = self._write_csv(CSV_TEXT, name="reuse-b.CSV")
        parsed = SyncService().inspect([duplicate_a, duplicate_b], options)
        service = PrecheckService()

        with patch.object(service.sync_service, "inspect") as inspect_mock:
            report = service.check_tracks(parsed)

        inspect_mock.assert_not_called()
        self.assertEqual(report.checked_count, 2)
        self.assertEqual(len(report.duplicate_groups), 1)
        self.assertEqual(set(report.duplicate_groups[0].source_paths), {duplicate_a, duplicate_b})
        self.assertEqual(report.file_errors, ())
        self.assertFalse(report.canceled)

    def test_precheck_check_tracks_keeps_existing_file_errors(self):
        options = SyncOptions(format_options=FormatOptions())
        track_path = self._write_csv(CSV_TEXT, name="reuse-error-a.CSV")
        parsed = SyncService().inspect([track_path], options)
        service = PrecheckService()
        bad_file = self._write_file("not a supported track\n", "reuse-error-b.txt")

        report = service.check_tracks(
            parsed,
            file_errors=[FileCheckError(source_path=bad_file, message="mock parse error")],
        )

        self.assertEqual(report.checked_count, 1)
        self.assertEqual(len(report.file_errors), 1)
        self.assertEqual(report.file_errors[0].source_path, bad_file)
        self.assertEqual(report.file_errors[0].message, "mock parse error")

    def test_precheck_check_tracks_and_check_consistent_on_cache_inputs(self):
        options = SyncOptions(format_options=FormatOptions())
        track_path = self._write_csv(CSV_TEXT_SHORT, name="cache-check.CSV")
        parsed = SyncService().inspect([track_path], options)
        service = PrecheckService()

        report = service.check_tracks(parsed)
        second_report = service.check_tracks(parsed)

        self.assertEqual(report.checked_count, second_report.checked_count)
        self.assertEqual(report.duplicate_groups, second_report.duplicate_groups)
        self.assertEqual(report.overlapping_points, second_report.overlapping_points)
        self.assertEqual(report.conflicting_points, second_report.conflicting_points)
        self.assertEqual(report.file_errors, second_report.file_errors)

    def _new_precheck_task_harness(self):
        harness = type("PrecheckTaskHarness", (), {})()
        harness._inspect_cancel_requested = False
        harness._local_track_cache = {}

        def tr(key: str, **kwargs):
            templates = {
                "precheck_local": "Running local pre-check for {count} files",
                "precheck_file": "Pre-checking file {index}/{count}: {name}",
                "precheck_issue_summary": "Local pre-check found duplicate, overlap, or conflict issues",
                "check_stopped": "Check stopped",
                "using_cached_track": "Using cached parse result: {name}",
            }
            text = templates.get(key, key)
            return text.format(**kwargs) if kwargs else text

        def track_cache_key(path: Path, options_obj: SyncOptions):
            stat = path.stat()
            return (stat.st_size, stat.st_mtime_ns, options_obj.format_options)

        def cache_local_track(path: Path, options_obj: SyncOptions, track):
            harness._local_track_cache[path] = (*track_cache_key(path, options_obj), track)

        def cached_local_track(path: Path, options_obj: SyncOptions):
            cached = harness._local_track_cache.get(path)
            if cached is None:
                return None
            size, mtime_ns, cached_format, track = cached
            current_size, current_mtime_ns, current_format = track_cache_key(path, options_obj)
            if (size, mtime_ns, cached_format) == (current_size, current_mtime_ns, current_format):
                return track
            harness._local_track_cache.pop(path, None)
            return None

        harness.tr = tr
        harness._cache_local_track = cache_local_track
        harness._cached_local_track = cached_local_track
        return harness

    def test_precheck_task_reuses_parsed_cache_between_checks(self):
        first = self._write_csv(CSV_TEXT, name="reuse-cache-a.CSV")
        second = self._write_csv(CSV_TEXT_SHORT, name="reuse-cache-b.CSV")
        options = SyncOptions(format_options=FormatOptions())
        events_first = []
        events_second = []
        harness = self._new_precheck_task_harness()
        task = MainWindow._precheck_task.__get__(harness, MainWindow)

        class TrackingSyncService(SyncService):
            inspect_calls = 0

            def inspect(self, files, options):
                self.__class__.inspect_calls += 1
                return super().inspect(files, options)

        TrackingSyncService.inspect_calls = 0
        with patch.object(gui_main, "SyncService", TrackingSyncService):
            task(
                [first, second],
                options,
                "Running local pre-check for {count} files",
                "Pre-checking file {index}/{count}: {name}",
                "Local pre-check passed",
                "Local pre-check found duplicate, overlap, or conflict issues",
                events_first.append,
            )
            self.assertEqual(TrackingSyncService.inspect_calls, 2)

            task(
                [first, second],
                options,
                "Running local pre-check for {count} files",
                "Pre-checking file {index}/{count}: {name}",
                "Local pre-check passed",
                "Local pre-check found duplicate, overlap, or conflict issues",
                events_second.append,
            )
            self.assertEqual(TrackingSyncService.inspect_calls, 2)

        self.assertEqual(len([event for event in events_first if event[0] == "track"]), 2)
        self.assertEqual(len([event for event in events_second if event[0] == "track"]), 2)
        self.assertEqual(
            [event for event in events_second if event[0] == "log" and event[1].startswith("Using cached parse result")],
            [
                ("log", "Using cached parse result: reuse-cache-a.CSV"),
                ("log", "Using cached parse result: reuse-cache-b.CSV"),
            ],
        )

    def test_precheck_task_reprocesses_modified_file_only(self):
        first = self._write_csv(CSV_TEXT, name="modify-cache-a.CSV")
        second = self._write_csv(CSV_TEXT_SHORT, name="modify-cache-b.CSV")
        options = SyncOptions(format_options=FormatOptions())
        events_after_modify = []
        harness = self._new_precheck_task_harness()
        task = MainWindow._precheck_task.__get__(harness, MainWindow)

        class TrackingSyncService(SyncService):
            inspect_calls = 0

            def inspect(self, files, options):
                self.__class__.inspect_calls += 1
                return super().inspect(files, options)

        TrackingSyncService.inspect_calls = 0
        with patch.object(gui_main, "SyncService", TrackingSyncService):
            task(
                [first, second],
                options,
                "Running local pre-check for {count} files",
                "Pre-checking file {index}/{count}: {name}",
                "Local pre-check passed",
                "Local pre-check found duplicate, overlap, or conflict issues",
                lambda e: None,
            )
            self.assertEqual(TrackingSyncService.inspect_calls, 2)

            first.write_text(f"{CSV_TEXT}\n", encoding="utf-8")

            task(
                [first, second],
                options,
                "Running local pre-check for {count} files",
                "Pre-checking file {index}/{count}: {name}",
                "Local pre-check passed",
                "Local pre-check found duplicate, overlap, or conflict issues",
                events_after_modify.append,
            )
            self.assertEqual(TrackingSyncService.inspect_calls, 3)

        self.assertTrue(any(event == ("log", "Using cached parse result: modify-cache-b.CSV") for event in events_after_modify))
        self.assertFalse(any(event == ("log", "Using cached parse result: modify-cache-a.CSV") for event in events_after_modify))

    def test_precheck_task_reuses_cached_tracks_when_file_list_changes(self):
        first = self._write_csv(CSV_TEXT, name="relist-cache-a.CSV")
        second = self._write_csv(CSV_TEXT_SHORT, name="relist-cache-b.CSV")
        third = self._write_csv(CSV_TEXT_SHORT, name="relist-cache-c.CSV")
        options = SyncOptions(format_options=FormatOptions())
        events_second = []
        harness = self._new_precheck_task_harness()
        task = MainWindow._precheck_task.__get__(harness, MainWindow)

        class TrackingSyncService(SyncService):
            inspect_calls = 0

            def inspect(self, files, options):
                self.__class__.inspect_calls += 1
                return super().inspect(files, options)

        TrackingSyncService.inspect_calls = 0
        with patch.object(gui_main, "SyncService", TrackingSyncService):
            task(
                [first, second],
                options,
                "Running local pre-check for {count} files",
                "Pre-checking file {index}/{count}: {name}",
                "Local pre-check passed",
                "Local pre-check found duplicate, overlap, or conflict issues",
                lambda e: None,
            )
            self.assertEqual(TrackingSyncService.inspect_calls, 2)

            task(
                [second, third],
                options,
                "Running local pre-check for {count} files",
                "Pre-checking file {index}/{count}: {name}",
                "Local pre-check passed",
                "Local pre-check found duplicate, overlap, or conflict issues",
                events_second.append,
            )
            self.assertEqual(TrackingSyncService.inspect_calls, 3)

        self.assertEqual(
            [event for event in events_second if event[0] == "log" and event[1].startswith("Using cached parse result")],
            [
                ("log", "Using cached parse result: relist-cache-b.CSV"),
            ],
        )

    def test_directory_without_subdirs_does_not_require_recursive_prompt(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / "file.txt").write_text("abc", encoding="utf-8")

        self.assertFalse(MainWindow._directory_has_children(root))

    def test_directory_with_subdirs_requires_recursive_prompt(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / "file.txt").write_text("abc", encoding="utf-8")
        (root / "nested").mkdir()

        self.assertTrue(MainWindow._directory_has_children(root))

    def test_precheck_can_be_canceled_between_files(self):
        first = self._write_csv(CSV_TEXT, name="cancel-a.CSV")
        second = self._write_csv(CSV_TEXT_SHORT, name="cancel-b.CSV")
        seen = []

        report = PrecheckService().check(
            [first, second],
            SyncOptions(format_options=FormatOptions()),
            on_file=lambda index, count, path: seen.append(path.name),
            should_cancel=lambda: bool(seen),
        )

        self.assertTrue(report.canceled)
        self.assertEqual(report.checked_count, 1)
        self.assertEqual(seen, ["cancel-a.CSV"])

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

    def test_backfill_unsigned_remote_activity_adds_token_only(self):
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

        self.assertEqual(decisions[0].status, "backfilled-token")
        self.assertEqual(garmin.updated_name, f"External activity {local_track.token}")
        self.assertEqual(decisions[0].planned_name, garmin.updated_name)

    def test_dry_run_unsigned_legacy_match_preserves_remote_name_and_adds_token(self):
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

        self.assertEqual(decisions[0].status, "skip-legacy-match")
        self.assertEqual(decisions[0].planned_name, f"External activity {local_track.token}")

    def test_signed_legacy_match_uses_planned_activity_name(self):
        path = self._write_csv(CSV_TEXT)
        local_track = SyncService().inspect([path], SyncOptions(format_options=FormatOptions()))[0]
        metadata = local_track.track_file.track.metadata
        remote = RemoteActivity(
            activity_id=656,
            activity_name="Old external-ish name",
            begin_timestamp_ms=int(metadata.start_time_utc.timestamp() * 1000),
            start_latitude=metadata.start_latitude,
            start_longitude=metadata.start_longitude,
            duration_s=metadata.duration_s,
            manufacturer=GCU_MANUFACTURER,
            device_id=GCU_DEVICE_ID,
        )
        garmin = ExistingActivityGarmin(remote)

        decisions = SyncService().sync([path], garmin, SyncOptions(format_options=FormatOptions(), dry_run=True))

        self.assertEqual(decisions[0].status, "skip-legacy-match")
        self.assertEqual(decisions[0].planned_name, local_track.planned_name)

    def test_legacy_match_with_existing_token_is_skipped(self):
        path = self._write_csv(CSV_TEXT)
        local_track = SyncService().inspect([path], SyncOptions(format_options=FormatOptions()))[0]
        metadata = local_track.track_file.track.metadata
        remote = RemoteActivity(
            activity_id=657,
            activity_name=f"Hangzhou Track Me {local_track.token}",
            begin_timestamp_ms=int(metadata.start_time_utc.timestamp() * 1000),
            start_latitude=metadata.start_latitude,
            start_longitude=metadata.start_longitude,
            duration_s=metadata.duration_s,
        )
        garmin = ExistingActivityGarmin(remote)

        decisions = SyncService().sync([path], garmin, SyncOptions(format_options=FormatOptions(), dry_run=True))

        self.assertEqual(decisions[0].status, "skip-token")
        self.assertEqual(decisions[0].activity_id, 657)

    def test_sync_uploads_larger_tracks_first(self):
        long_path = self._write_csv(CSV_TEXT, name="long.CSV")
        short_path = self._write_csv(CSV_TEXT_SHORT, name="short.CSV")
        service = SyncService()
        options = SyncOptions(format_options=FormatOptions())
        long_name = service.inspect([long_path], options)[0].planned_name
        garmin = ImmediateUploadGarmin()

        service.sync([short_path, long_path], garmin, options)

        self.assertEqual(garmin.updated_names[0], long_name)

    def test_sync_can_preserve_input_upload_order(self):
        long_path = self._write_csv(CSV_TEXT, name="long.CSV")
        short_path = self._write_csv(CSV_TEXT_SHORT, name="short.CSV")
        service = SyncService()
        options = SyncOptions(format_options=FormatOptions(), sort_for_upload=False)
        short_name = service.inspect([short_path], options)[0].planned_name
        garmin = ImmediateUploadGarmin()

        service.sync([short_path, long_path], garmin, options)

        self.assertEqual(garmin.updated_names[0], short_name)

    def test_sync_continues_after_single_upload_exception(self):
        first = self._write_csv(CSV_TEXT, name="upload-fails.CSV")
        second = self._write_csv(CSV_TEXT_SHORT, name="upload-ok.CSV")
        garmin = FirstUploadFailsGarmin()

        decisions = SyncService().sync([first, second], garmin, SyncOptions(format_options=FormatOptions()))

        self.assertEqual(len(decisions), 2)
        self.assertEqual(decisions[0].status, "failed")
        self.assertIn("RuntimeError: upload failed", decisions[0].message)
        self.assertEqual(decisions[1].status, "upload")
        self.assertEqual(garmin.upload_count, 2)

    def test_sync_reports_upload_progress_events(self):
        path = self._write_csv(CSV_TEXT, name="progress-upload.CSV")
        local_track = SyncService().inspect([path], SyncOptions(format_options=FormatOptions()))[0]
        garmin = ImmediateUploadGarmin()
        events = []

        SyncService().sync_tracks(
            [local_track],
            garmin,
            SyncOptions(format_options=FormatOptions()),
            on_progress=lambda event, track, details: events.append(event),
        )

        self.assertIn("planning", events)
        self.assertIn("write-fit", events)
        self.assertIn("upload", events)
        self.assertIn("wait-uploaded", events)
        self.assertIn("update-name", events)

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

    def test_remote_token_match_has_priority_over_signature(self):
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

        self.assertEqual(decisions[0].status, "skip-token")
        self.assertEqual(decisions[0].activity_id, 777)

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

    def test_upload_without_activity_id_waits_and_tags_before_next_track(self):
        path = self._write_csv(CSV_TEXT)
        service = SyncService()
        options = SyncOptions(format_options=FormatOptions())
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

    def test_sync_uploads_serially_until_activity_is_tagged(self):
        first = self._write_csv(CSV_TEXT, name="serial-a.CSV")
        second = self._write_csv(CSV_TEXT_SHORT, name="serial-b.CSV")
        service = SyncService()
        tracks = service.inspect([first, second], SyncOptions(format_options=FormatOptions()))
        garmin = SerialOrderGarmin(tracks)

        decisions = service.sync_tracks(tracks, garmin, SyncOptions(format_options=FormatOptions()))

        self.assertEqual([decision.status for decision in decisions], ["upload", "upload"])
        self.assertLess(garmin.events.index("update:1"), garmin.events.index("upload:2"))

    def test_sync_cancel_stops_before_next_track(self):
        first = self._write_csv(CSV_TEXT, name="cancel-a.CSV")
        second = self._write_csv(CSV_TEXT_SHORT, name="cancel-b.CSV")
        service = SyncService()
        tracks = service.inspect([first, second], SyncOptions(format_options=FormatOptions()))
        garmin = SerialOrderGarmin(tracks)
        decision_count = 0

        def on_decision(_decision):
            nonlocal decision_count
            decision_count += 1

        decisions = service.sync_tracks(
            tracks,
            garmin,
            SyncOptions(format_options=FormatOptions()),
            on_decision=on_decision,
            should_cancel=lambda: decision_count >= 1,
        )

        self.assertEqual([decision.status for decision in decisions], ["upload"])
        self.assertEqual(garmin.events, ["upload:1", "update:1"])

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


class FakeMainWindowHarness:
    _table_files = MainWindow._table_files
    _runnable_plan_files = MainWindow._runnable_plan_files


class FakeItem:
    def __init__(self, file_path: str | None = None, status: str | None = None):
        self.file_path = file_path
        self.status = status

    def data(self, role):
        if role == gui_main.Qt.UserRole:
            return self.file_path
        if role == PLAN_STATUS_ROLE:
            return self.status
        return None


class FakeTable:
    def __init__(self, rows):
        self.rows = rows

    def rowCount(self):
        return len(self.rows)

    def item(self, row, column):
        return self.rows[row].get(column)


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


class FirstUploadFailsGarmin(ImmediateUploadGarmin):
    def upload_activity(self, file_path):
        self.upload_count += 1
        if self.upload_count == 1:
            raise RuntimeError("upload failed")
        self.next_id += 1
        self.activities.append(
            RemoteActivity(
                activity_id=self.next_id,
                activity_name="Uploaded activity",
                manufacturer=GCU_MANUFACTURER,
                device_id=GCU_DEVICE_ID,
            )
        )
        return UploadResult(activity_id=self.next_id)


class DeferredLookupGarmin:
    def __init__(self, remote: RemoteActivity):
        self.remote = remote
        self.updated_name = ""
        self.uploaded = False

    def list_activities(self, start_date, end_date):
        return [self.remote] if self.uploaded else []

    def upload_activity(self, file_path):
        self.uploaded = True
        return UploadResult(activity_id=None)

    def update_activity_name(self, activity_id: int, activity_name: str):
        self.updated_name = activity_name


class SerialOrderGarmin:
    def __init__(self, tracks: list):
        self.events: list[str] = []
        self.next_upload = 0
        self.activities: list[RemoteActivity] = []
        self.by_id: dict[int, RemoteActivity] = {}
        for activity_id, track in enumerate(tracks, start=1):
            metadata = track.track_file.track.metadata
            activity = RemoteActivity(
                activity_id=activity_id,
                activity_name="Uploaded activity",
                begin_timestamp_ms=int(metadata.start_time_utc.timestamp() * 1000),
                start_latitude=metadata.start_latitude,
                start_longitude=metadata.start_longitude,
                duration_s=metadata.duration_s,
                manufacturer=GCU_MANUFACTURER,
                device_id=GCU_DEVICE_ID,
            )
            self.by_id[activity_id] = activity

    def list_activities(self, start_date, end_date):
        return list(self.activities)

    def upload_activity(self, file_path):
        self.next_upload += 1
        self.events.append(f"upload:{self.next_upload}")
        activity = self.by_id[self.next_upload]
        self.activities.append(activity)
        return UploadResult(activity_id=activity.activity_id)

    def update_activity_name(self, activity_id: int, activity_name: str):
        self.events.append(f"update:{activity_id}")


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
