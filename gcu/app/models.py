from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TrackPoint:
    timestamp_utc: datetime
    latitude: float
    longitude: float
    altitude_m: float | None = None
    speed_mps: float | None = None
    heading_deg: float | None = None
    accuracy_m: float | None = None
    raw_extensions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrackMetadata:
    start_time_utc: datetime
    end_time_utc: datetime
    duration_s: float
    point_count: int
    start_latitude: float
    start_longitude: float
    end_latitude: float
    end_longitude: float
    display_name: str
    source_device: str | None = None
    display_timezone: str = "UTC"
    display_city: str | None = None


@dataclass(frozen=True)
class Track:
    points: tuple[TrackPoint, ...]
    metadata: TrackMetadata


@dataclass(frozen=True)
class TrackFile:
    source_path: Path
    source_format: str
    track: Track
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class LocalTrack:
    track_file: TrackFile
    token: str
    digest: str
    planned_name: str


@dataclass(frozen=True)
class RemoteActivity:
    activity_id: int
    activity_name: str
    begin_timestamp_ms: int | None = None
    start_latitude: float | None = None
    start_longitude: float | None = None
    duration_s: float | None = None
    activity_type: str | None = None
    manufacturer: str | None = None
    device_id: int | None = None


@dataclass(frozen=True)
class SyncDecision:
    source_path: Path
    status: str
    token: str
    planned_name: str
    activity_id: int | None = None
    message: str = ""
    candidates: tuple[RemoteActivity, ...] = ()


@dataclass(frozen=True)
class PurgeDecision:
    activity_id: int
    activity_name: str
    status: str
    manufacturer: str | None = None
    device_id: int | None = None
    begin_timestamp_ms: int | None = None
    duration_s: float | None = None
    message: str = ""


@dataclass(frozen=True)
class PurgeSummary:
    start_date: date
    end_date: date
    scanned_count: int
    matched_count: int
    deleted_count: int
    skipped_unsigned_count: int
    dry_run: bool
    decisions: tuple[PurgeDecision, ...] = ()


@dataclass(frozen=True)
class BackupDecision:
    activity_id: int
    activity_name: str
    status: str
    output_path: Path | None = None
    manufacturer: str | None = None
    device_id: int | None = None
    begin_timestamp_ms: int | None = None
    duration_s: float | None = None
    message: str = ""


@dataclass(frozen=True)
class BackupSummary:
    start_date: date
    end_date: date
    scanned_count: int
    matched_count: int
    downloaded_count: int
    skipped_count: int
    output_dir: Path
    decisions: tuple[BackupDecision, ...] = ()


@dataclass(frozen=True)
class DuplicateTrackGroup:
    token: str
    source_paths: tuple[Path, ...]


@dataclass(frozen=True)
class PointRelation:
    first_source_path: Path
    second_source_path: Path
    timestamp_utc: datetime
    first_latitude: float
    first_longitude: float
    second_latitude: float
    second_longitude: float


@dataclass(frozen=True)
class PointRelationSummary:
    first_source_path: Path
    second_source_path: Path
    count: int
    examples: tuple[PointRelation, ...] = ()


@dataclass(frozen=True)
class FileCheckError:
    source_path: Path
    message: str


@dataclass(frozen=True)
class PrecheckReport:
    checked_count: int
    duplicate_groups: tuple[DuplicateTrackGroup, ...] = ()
    overlapping_points: tuple[PointRelationSummary, ...] = ()
    conflicting_points: tuple[PointRelationSummary, ...] = ()
    file_errors: tuple[FileCheckError, ...] = ()
    canceled: bool = False


@dataclass(frozen=True)
class UploadResult:
    activity_id: int | None = None
    raw: Any = None


@dataclass(frozen=True)
class AuthenticatedUser:
    username: str
    email: str = ""
    display_name: str = ""
    full_name: str = ""
    profile_id: int | None = None
