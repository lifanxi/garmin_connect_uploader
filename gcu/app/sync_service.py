from __future__ import annotations

import tempfile
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil
from pathlib import Path
from typing import Protocol

from gcu.app.models import LocalTrack, PurgeDecision, PurgeSummary, RemoteActivity, SyncDecision
from gcu.app.naming import planned_activity_name
from gcu.duplicate.fingerprint import append_or_replace_token, fingerprint_track
from gcu.duplicate.matcher import MatchOptions, find_legacy_matches
from gcu.duplicate.remote_index import RemoteActivityIndex
from gcu.export.fit_writer import write_fit
from gcu.formats.base import FormatOptions, get_reader
from gcu.garmin.errors import DuplicateUploadError
from gcu.garmin.signature import is_gcu_activity


class GarminGateway(Protocol):
    def list_activities(self, start_date: date, end_date: date) -> list[RemoteActivity]:
        ...

    def upload_activity(self, file_path: Path):
        ...

    def update_activity_name(self, activity_id: int, activity_name: str):
        ...

    def delete_activity(self, activity_id: int):
        ...


@dataclass(frozen=True)
class SyncOptions:
    format_options: FormatOptions
    match_options: MatchOptions = MatchOptions()
    dry_run: bool = False
    name_template: str | None = None
    keep_fit: bool = False
    output_dir: Path | None = None
    post_upload_wait_base_s: int = 30
    post_upload_wait_per_1000_points_s: int = 5
    post_upload_max_wait_s: int = 180
    post_upload_tag_workers: int = 4


class SyncService:
    def inspect(self, files: list[Path], options: SyncOptions) -> list[LocalTrack]:
        return [self._load_local_track(path, options) for path in files]

    def plan(self, files: list[Path], garmin: GarminGateway, options: SyncOptions) -> list[SyncDecision]:
        local_tracks = self.inspect(files, options)
        if not local_tracks:
            return []
        start_date, end_date = self._query_window(local_tracks)
        activities = garmin.list_activities(start_date, end_date)
        index = RemoteActivityIndex.build(activities)
        return [self._decide(local_track, index, options) for local_track in local_tracks]

    def sync(self, files: list[Path], garmin: GarminGateway, options: SyncOptions) -> list[SyncDecision]:
        local_tracks = self.inspect(files, options)
        if not local_tracks:
            return []
        local_tracks = self._sort_for_upload(local_tracks)
        start_date, end_date = self._query_window(local_tracks)
        index = RemoteActivityIndex.build(garmin.list_activities(start_date, end_date))
        decisions: list[SyncDecision] = []
        pending_tagging: dict[Future[SyncDecision], Path | None] = {}
        processed_tokens: dict[str, int | None] = {}

        worker_count = max(1, options.post_upload_tag_workers)
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for local_track in local_tracks:
                if local_track.token in processed_tokens:
                    decisions.append(
                        SyncDecision(
                            source_path=local_track.track_file.source_path,
                            status="skip-token",
                            token=local_track.token,
                            planned_name=local_track.planned_name,
                            activity_id=processed_tokens[local_track.token],
                            message="duplicate local track in same batch",
                        )
                    )
                    continue

                decision = self._decide(local_track, index, options)
                if decision.status == "skip-legacy-match" and not options.dry_run and decision.activity_id is not None:
                    match = self._single_candidate(decision)
                    if match is not None and not is_gcu_activity(match):
                        decisions.append(self._signature_rejected_decision(local_track, match))
                        continue
                    garmin.update_activity_name(decision.activity_id, decision.planned_name)
                    processed_tokens[local_track.token] = decision.activity_id
                    decisions.append(
                        SyncDecision(
                            source_path=decision.source_path,
                            status="backfilled-token",
                            token=decision.token,
                            planned_name=decision.planned_name,
                            activity_id=decision.activity_id,
                            message="token added to existing activity",
                        )
                    )
                    continue
                if decision.status != "upload" or options.dry_run:
                    if decision.status in {"skip-token", "skip-legacy-match"}:
                        processed_tokens[local_track.token] = decision.activity_id
                    decisions.append(decision)
                    continue

                fit_path = self._fit_path(local_track, options)
                write_fit(local_track.track_file.track, fit_path, activity_name=local_track.planned_name)
                try:
                    upload_result = garmin.upload_activity(fit_path)
                except DuplicateUploadError:
                    conflict_decision = self._resolve_upload_conflict(local_track, garmin, options)
                    if conflict_decision.activity_id is not None and conflict_decision.status == "upload-conflict":
                        processed_tokens[local_track.token] = conflict_decision.activity_id
                    decisions.append(conflict_decision)
                    self._cleanup_fit(fit_path, options)
                    continue

                activity_id = getattr(upload_result, "activity_id", None)
                if activity_id is not None:
                    wait_s = self._estimated_post_upload_wait_s(local_track, options)
                    uploaded_activity = self._wait_for_uploaded_activity(local_track, garmin, wait_s, activity_id)
                    if uploaded_activity is None:
                        decisions.append(
                            SyncDecision(
                                source_path=local_track.track_file.source_path,
                                status="upload",
                                token=local_track.token,
                                planned_name=local_track.planned_name,
                                activity_id=activity_id,
                                message=f"uploaded; activity signature unavailable after {wait_s}s, not tagged",
                            )
                        )
                        processed_tokens[local_track.token] = activity_id
                        self._cleanup_fit(fit_path, options)
                        continue
                    if not is_gcu_activity(uploaded_activity):
                        decisions.append(self._signature_rejected_decision(local_track, uploaded_activity))
                        self._cleanup_fit(fit_path, options)
                        continue
                    garmin.update_activity_name(activity_id, local_track.planned_name)
                    processed_tokens[local_track.token] = activity_id
                    decisions.append(
                        SyncDecision(
                            source_path=local_track.track_file.source_path,
                            status="upload",
                            token=local_track.token,
                            planned_name=local_track.planned_name,
                            activity_id=activity_id,
                            message="uploaded",
                        )
                    )
                    self._cleanup_fit(fit_path, options)
                    continue

                processed_tokens[local_track.token] = None
                future = executor.submit(self._tag_after_upload, local_track, garmin, options)
                pending_tagging[future] = fit_path

            for future in as_completed(pending_tagging):
                fit_path = pending_tagging[future]
                try:
                    decisions.append(future.result())
                finally:
                    if fit_path is not None:
                        self._cleanup_fit(fit_path, options)

        return decisions

    def _tag_after_upload(
        self,
        local_track: LocalTrack,
        garmin: GarminGateway,
        options: SyncOptions,
    ) -> SyncDecision:
        wait_s = self._estimated_post_upload_wait_s(local_track, options)
        located = self._locate_uploaded_activity(local_track, garmin, wait_s)
        activity_id = located.activity_id if located else None
        if activity_id is not None:
            if located is not None and not is_gcu_activity(located):
                return self._signature_rejected_decision(local_track, located)
            garmin.update_activity_name(activity_id, local_track.planned_name)
        return (
            SyncDecision(
                source_path=local_track.track_file.source_path,
                status="upload",
                token=local_track.token,
                planned_name=local_track.planned_name,
                activity_id=activity_id,
                message=f"uploaded; token tagged after waiting up to {wait_s}s",
            )
            if activity_id is not None
            else SyncDecision(
                source_path=local_track.track_file.source_path,
                status="upload",
                token=local_track.token,
                planned_name=local_track.planned_name,
                message=f"uploaded; activity id unavailable for tagging after {wait_s}s",
            )
        )

    def backfill(self, files: list[Path], garmin: GarminGateway, options: SyncOptions) -> list[SyncDecision]:
        local_tracks = self.inspect(files, options)
        if not local_tracks:
            return []
        start_date, end_date = self._query_window(local_tracks)
        index = RemoteActivityIndex.build(garmin.list_activities(start_date, end_date))
        decisions: list[SyncDecision] = []
        for local_track in local_tracks:
            existing = index.by_token.get(local_track.token)
            if existing:
                decisions.append(
                    SyncDecision(
                        source_path=local_track.track_file.source_path,
                        status="skip-token",
                        token=local_track.token,
                        planned_name=local_track.planned_name,
                        activity_id=existing.activity_id,
                        message="token already exists",
                    )
                )
                continue
            matches = find_legacy_matches(local_track, index, options.match_options)
            if len(matches) == 1:
                match = matches[0]
                if not is_gcu_activity(match):
                    decisions.append(self._signature_rejected_decision(local_track, match))
                    continue
                new_name = append_or_replace_token(match.activity_name, local_track.token)
                if not options.dry_run:
                    garmin.update_activity_name(match.activity_id, new_name)
                decisions.append(
                    SyncDecision(
                        source_path=local_track.track_file.source_path,
                        status="backfilled-token",
                        token=local_track.token,
                        planned_name=new_name,
                        activity_id=match.activity_id,
                        message="token backfilled" if not options.dry_run else "would backfill token",
                    )
                )
            elif len(matches) > 1:
                decisions.append(
                    SyncDecision(
                        source_path=local_track.track_file.source_path,
                        status="ambiguous",
                        token=local_track.token,
                        planned_name=local_track.planned_name,
                        message="multiple legacy matches",
                        candidates=matches,
                    )
                )
            else:
                decisions.append(
                    SyncDecision(
                        source_path=local_track.track_file.source_path,
                        status="failed",
                        token=local_track.token,
                        planned_name=local_track.planned_name,
                        message="no matching remote activity",
                    )
                )
        return decisions

    def purge(
        self,
        garmin: GarminGateway,
        start_date: date,
        end_date: date,
        dry_run: bool = False,
        chunk_days: int = 366,
    ) -> PurgeSummary:
        decisions: list[PurgeDecision] = []
        skipped_unsigned_count = 0
        deleted_count = 0
        scanned_count = 0
        seen_activity_ids: set[int] = set()

        for chunk_start, chunk_end in _date_chunks(start_date, end_date, max(1, chunk_days)):
            activities = garmin.list_activities(chunk_start, chunk_end)
            for activity in activities:
                if activity.activity_id in seen_activity_ids:
                    continue
                seen_activity_ids.add(activity.activity_id)
                scanned_count += 1
                if not is_gcu_activity(activity):
                    skipped_unsigned_count += 1
                    continue

                if dry_run:
                    decisions.append(
                        PurgeDecision(
                            activity_id=activity.activity_id,
                            activity_name=activity.activity_name,
                            status="would-delete",
                            manufacturer=activity.manufacturer,
                            device_id=activity.device_id,
                            message="signed GCU activity",
                        )
                    )
                    continue

                garmin.delete_activity(activity.activity_id)
                deleted_count += 1
                decisions.append(
                    PurgeDecision(
                        activity_id=activity.activity_id,
                        activity_name=activity.activity_name,
                        status="deleted",
                        manufacturer=activity.manufacturer,
                        device_id=activity.device_id,
                        message="signed GCU activity deleted",
                    )
                )

        return PurgeSummary(
            start_date=start_date,
            end_date=end_date,
            scanned_count=scanned_count,
            matched_count=len(decisions),
            deleted_count=deleted_count,
            skipped_unsigned_count=skipped_unsigned_count,
            dry_run=dry_run,
            decisions=tuple(decisions),
        )

    def _load_local_track(self, path: Path, options: SyncOptions) -> LocalTrack:
        reader = get_reader(path, options.format_options)
        track_file = reader.read(path, options.format_options)
        digest, token = fingerprint_track(track_file.track)
        name = planned_activity_name(track_file.track, token, options.name_template)
        return LocalTrack(track_file=track_file, token=token, digest=digest, planned_name=name)

    def _decide(
        self,
        local_track: LocalTrack,
        index: RemoteActivityIndex,
        options: SyncOptions,
    ) -> SyncDecision:
        token_match = index.by_token.get(local_track.token)
        if token_match:
            return SyncDecision(
                source_path=local_track.track_file.source_path,
                status="skip-token",
                token=local_track.token,
                planned_name=local_track.planned_name,
                activity_id=token_match.activity_id,
                message="remote token match",
            )

        legacy_matches = find_legacy_matches(local_track, index, options.match_options)
        if len(legacy_matches) == 1:
            match = legacy_matches[0]
            if not is_gcu_activity(match):
                return self._signature_rejected_decision(local_track, match)
            new_name = append_or_replace_token(match.activity_name, local_track.token)
            return SyncDecision(
                source_path=local_track.track_file.source_path,
                status="skip-legacy-match",
                token=local_track.token,
                planned_name=new_name,
                activity_id=match.activity_id,
                message="legacy activity match",
                candidates=(match,),
            )
        if len(legacy_matches) > 1:
            return SyncDecision(
                source_path=local_track.track_file.source_path,
                status="ambiguous",
                token=local_track.token,
                planned_name=local_track.planned_name,
                message="multiple legacy matches",
                candidates=legacy_matches,
            )
        return SyncDecision(
            source_path=local_track.track_file.source_path,
            status="upload",
            token=local_track.token,
            planned_name=local_track.planned_name,
            message="no duplicate found",
        )

    def _query_window(self, local_tracks: list[LocalTrack]) -> tuple[date, date]:
        dates = [item.track_file.track.metadata.start_time_utc.date() for item in local_tracks]
        return min(dates) - timedelta(days=1), max(dates) + timedelta(days=1)

    def _sort_for_upload(self, local_tracks: list[LocalTrack]) -> list[LocalTrack]:
        return sorted(
            local_tracks,
            key=lambda item: item.track_file.track.metadata.point_count,
            reverse=True,
        )

    def _estimated_post_upload_wait_s(self, local_track: LocalTrack, options: SyncOptions) -> int:
        point_count = local_track.track_file.track.metadata.point_count
        estimated = options.post_upload_wait_base_s + (
            ceil(point_count / 1000) * options.post_upload_wait_per_1000_points_s
        )
        return max(0, min(options.post_upload_max_wait_s, estimated))

    def _cleanup_fit(self, fit_path: Path, options: SyncOptions) -> None:
        if not options.keep_fit and options.output_dir is None:
            fit_path.unlink(missing_ok=True)

    def _fit_path(self, local_track: LocalTrack, options: SyncOptions) -> Path:
        source = local_track.track_file.source_path
        if options.output_dir:
            options.output_dir.mkdir(parents=True, exist_ok=True)
            return options.output_dir / source.with_suffix(".fit").name
        handle = tempfile.NamedTemporaryFile(prefix=f"{source.stem}-", suffix=".fit", delete=False)
        handle.close()
        return Path(handle.name)

    def _locate_uploaded_activity(
        self,
        local_track: LocalTrack,
        garmin: GarminGateway,
        max_wait_s: int,
    ) -> RemoteActivity | None:
        if not hasattr(garmin, "wait_for_activity_match"):
            return None
        metadata = local_track.track_file.track.metadata
        local_start_ms = int(metadata.start_time_utc.timestamp() * 1000)

        def predicate(activity: RemoteActivity) -> bool:
            if activity.begin_timestamp_ms is None:
                return False
            if activity.start_latitude is None or activity.start_longitude is None:
                return False
            time_diff_s = abs(activity.begin_timestamp_ms - local_start_ms) / 1000
            lat_diff = abs(activity.start_latitude - metadata.start_latitude)
            lon_diff = abs(activity.start_longitude - metadata.start_longitude)
            if time_diff_s > self._default_post_upload_time_tolerance_s:
                return False
            if lat_diff > self._default_post_upload_coord_tolerance_deg:
                return False
            if lon_diff > self._default_post_upload_coord_tolerance_deg:
                return False
            return True

        return garmin.wait_for_activity_match(
            metadata.start_time_utc.date(),
            predicate,
            max_wait_s=max_wait_s,
        )

    def _wait_for_uploaded_activity(
        self,
        local_track: LocalTrack,
        garmin: GarminGateway,
        max_wait_s: int,
        activity_id: int | None = None,
    ) -> RemoteActivity | None:
        metadata = local_track.track_file.track.metadata
        local_start_ms = int(metadata.start_time_utc.timestamp() * 1000)
        started = time.time()
        delays = [1, 2, 3, 5, 5, 5, 5]

        def matches_track(activity: RemoteActivity) -> bool:
            if activity.begin_timestamp_ms is None:
                return False
            if activity.start_latitude is None or activity.start_longitude is None:
                return False
            time_diff_s = abs(activity.begin_timestamp_ms - local_start_ms) / 1000
            lat_diff = abs(activity.start_latitude - metadata.start_latitude)
            lon_diff = abs(activity.start_longitude - metadata.start_longitude)
            return (
                time_diff_s <= self._default_post_upload_time_tolerance_s
                and lat_diff <= self._default_post_upload_coord_tolerance_deg
                and lon_diff <= self._default_post_upload_coord_tolerance_deg
            )

        while True:
            for activity in self._list_nearby_activities(local_track, garmin):
                if activity_id is not None and activity.activity_id == activity_id:
                    return activity
                if matches_track(activity):
                    return activity
            elapsed = time.time() - started
            delay = delays[0] if delays else 5
            if elapsed + delay > max_wait_s:
                return None
            time.sleep(delay)
            if delays:
                delays = delays[1:]

    def _list_nearby_activities(
        self,
        local_track: LocalTrack,
        garmin: GarminGateway,
    ) -> list[RemoteActivity]:
        metadata = local_track.track_file.track.metadata
        return garmin.list_activities(
            metadata.start_time_utc.date() - timedelta(days=1),
            metadata.start_time_utc.date() + timedelta(days=1),
        )

    def _resolve_upload_conflict(
        self,
        local_track: LocalTrack,
        garmin: GarminGateway,
        options: SyncOptions,
    ) -> SyncDecision:
        metadata = local_track.track_file.track.metadata
        activities = garmin.list_activities(
            metadata.start_time_utc.date() - timedelta(days=1),
            metadata.start_time_utc.date() + timedelta(days=1),
        )
        index = RemoteActivityIndex.build(activities)
        matches = find_legacy_matches(local_track, index, options.match_options)
        if len(matches) == 1:
            match = matches[0]
            if not is_gcu_activity(match):
                return self._signature_rejected_decision(local_track, match)
            new_name = append_or_replace_token(match.activity_name, local_track.token)
            garmin.update_activity_name(match.activity_id, new_name)
            return SyncDecision(
                source_path=local_track.track_file.source_path,
                status="upload-conflict",
                token=local_track.token,
                planned_name=new_name,
                activity_id=match.activity_id,
                message="Garmin reported duplicate; token added to matched activity",
            )
        if len(matches) > 1:
            return SyncDecision(
                source_path=local_track.track_file.source_path,
                status="ambiguous",
                token=local_track.token,
                planned_name=local_track.planned_name,
                message="Garmin reported duplicate; multiple remote matches",
                candidates=matches,
            )
        return SyncDecision(
            source_path=local_track.track_file.source_path,
            status="upload-conflict",
            token=local_track.token,
            planned_name=local_track.planned_name,
            message="Garmin reported duplicate; no unique remote match",
        )

    _default_post_upload_time_tolerance_s = 60
    _default_post_upload_coord_tolerance_deg = 0.001

    def _single_candidate(self, decision: SyncDecision) -> RemoteActivity | None:
        return decision.candidates[0] if len(decision.candidates) == 1 else None

    def _signature_rejected_decision(self, local_track: LocalTrack, activity: RemoteActivity) -> SyncDecision:
        return SyncDecision(
            source_path=local_track.track_file.source_path,
            status="failed",
            token=local_track.token,
            planned_name=local_track.planned_name,
            activity_id=activity.activity_id,
            message=(
                "matched remote activity is not signed as GCU upload "
                f"(manufacturer={activity.manufacturer!r}, deviceId={activity.device_id!r}); refusing to modify"
            ),
        )


def _date_chunks(start_date: date, end_date: date, chunk_days: int):
    current = start_date
    while current <= end_date:
        chunk_end = min(end_date, current + timedelta(days=chunk_days - 1))
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)
