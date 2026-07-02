from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from gcu.app.models import AuthenticatedUser, LocalTrack, PrecheckReport, PurgeSummary, SyncDecision


def print_local_tracks(items: list[LocalTrack], as_json: bool = False) -> None:
    if as_json:
        print(json.dumps([_local_track_summary(item) for item in items], ensure_ascii=False, indent=2))
        return
    for item in items:
        metadata = item.track_file.track.metadata
        print(f"{item.track_file.source_path}")
        print(f"  format: {item.track_file.source_format}")
        print(f"  points: {metadata.point_count}")
        print(f"  start:  {metadata.start_time_utc.isoformat()}")
        print(f"  end:    {metadata.end_time_utc.isoformat()}")
        print(f"  display timezone: {metadata.display_timezone}")
        print(f"  display city: {metadata.display_city or '-'}")
        print(f"  token:  {item.token}")
        print(f"  name:   {item.planned_name}")
        for warning in item.track_file.warnings:
            print(f"  warning: {warning}")


def print_decisions(items: list[SyncDecision], as_json: bool = False) -> None:
    if as_json:
        print(json.dumps([_decision_summary(item) for item in items], ensure_ascii=False, indent=2))
        return

    width = max([len(item.status) for item in items] + [6])
    for item in items:
        activity = f" activity={item.activity_id}" if item.activity_id is not None else ""
        message = f" {item.message}" if item.message else ""
        print(f"{item.status:<{width}} {item.source_path}{activity}{message}")


def print_purge_summary(summary: PurgeSummary, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(_jsonable(summary), ensure_ascii=False, indent=2))
        return

    action = "would delete" if summary.dry_run else "deleted"
    print(
        f"scanned={summary.scanned_count} matched={summary.matched_count} "
        f"skipped_unsigned={summary.skipped_unsigned_count} {action}={len(summary.decisions)}"
    )
    width = max([len(item.status) for item in summary.decisions] + [6])
    for item in summary.decisions:
        print(
            f"{item.status:<{width}} activity={item.activity_id} "
            f"manufacturer={item.manufacturer} deviceId={item.device_id} {item.activity_name}"
        )


def print_precheck_report(report: PrecheckReport, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(_jsonable(report), ensure_ascii=False, indent=2))
        return

    print(f"checked={report.checked_count}")
    print(f"file_errors={len(report.file_errors)}")
    for item in report.file_errors:
        print(f"  {item.source_path}: {item.message}")

    print(f"duplicate_track_groups={len(report.duplicate_groups)}")
    for group in report.duplicate_groups:
        print(f"  token={group.token}")
        for path in group.source_paths:
            print(f"    {path}")

    print(f"overlapping_point_pairs={len(report.overlapping_points)}")
    for item in report.overlapping_points:
        print(f"  {item.first_source_path} <-> {item.second_source_path}: {item.count} same points")
        for example in item.examples:
            print(
                "    "
                f"{example.timestamp_utc.isoformat()} "
                f"lat={example.first_latitude:.7f} lon={example.first_longitude:.7f}"
            )

    print(f"conflicting_point_pairs={len(report.conflicting_points)}")
    for item in report.conflicting_points:
        print(f"  {item.first_source_path} <-> {item.second_source_path}: {item.count} conflicting points")
        for example in item.examples:
            print(
                "    "
                f"{example.timestamp_utc.isoformat()} "
                f"first=({example.first_latitude:.7f},{example.first_longitude:.7f}) "
                f"second=({example.second_latitude:.7f},{example.second_longitude:.7f})"
            )


def print_authenticated_user(user: AuthenticatedUser, domain: str, session_dir: Path, as_json: bool = False) -> None:
    summary = {
        "domain": domain,
        "session_dir": str(session_dir),
        "username": user.username,
        "email": user.email,
        "display_name": user.display_name,
        "full_name": user.full_name,
        "profile_id": user.profile_id,
    }
    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    username = user.email or user.username or "-"
    display = f" display_name={user.display_name}" if user.display_name else ""
    print(f"Garmin session is usable for {domain}: username={username}{display}")
    print(f"Garmin session saved in {session_dir}")


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _local_track_summary(item: LocalTrack) -> dict[str, Any]:
    metadata = item.track_file.track.metadata
    return {
        "source_path": str(item.track_file.source_path),
        "source_format": item.track_file.source_format,
        "point_count": metadata.point_count,
        "start_time_utc": metadata.start_time_utc.isoformat(),
        "end_time_utc": metadata.end_time_utc.isoformat(),
        "duration_s": metadata.duration_s,
        "start_latitude": metadata.start_latitude,
        "start_longitude": metadata.start_longitude,
        "end_latitude": metadata.end_latitude,
        "end_longitude": metadata.end_longitude,
        "display_timezone": metadata.display_timezone,
        "display_city": metadata.display_city,
        "token": item.token,
        "digest": item.digest,
        "planned_name": item.planned_name,
        "warnings": list(item.track_file.warnings),
    }


def _decision_summary(item: SyncDecision) -> dict[str, Any]:
    return {
        "source_path": str(item.source_path),
        "status": item.status,
        "token": item.token,
        "planned_name": item.planned_name,
        "activity_id": item.activity_id,
        "message": item.message,
        "candidates": [
            {
                "activity_id": candidate.activity_id,
                "activity_name": candidate.activity_name,
                "begin_timestamp_ms": candidate.begin_timestamp_ms,
                "manufacturer": candidate.manufacturer,
                "device_id": candidate.device_id,
            }
            for candidate in item.candidates
        ],
    }
