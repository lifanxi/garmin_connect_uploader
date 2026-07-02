from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from gcu.app.models import (
    DuplicateTrackGroup,
    FileCheckError,
    LocalTrack,
    PointRelation,
    PointRelationSummary,
    PrecheckReport,
    TrackPoint,
)
from gcu.app.sync_service import SyncOptions, SyncService


@dataclass(frozen=True)
class _PointRef:
    source_path: Path
    point: TrackPoint


class PrecheckService:
    def __init__(self, sync_service: SyncService | None = None):
        self.sync_service = sync_service or SyncService()

    def check(
        self,
        files: list[Path],
        options: SyncOptions,
        on_file: Callable[[int, int, Path], None] | None = None,
        on_track: Callable[[LocalTrack], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> PrecheckReport:
        local_tracks: list[LocalTrack] = []
        file_errors: list[FileCheckError] = []
        canceled = False
        count = len(files)
        for index, path in enumerate(files, start=1):
            if should_cancel is not None and should_cancel():
                canceled = True
                break
            if on_file is not None:
                on_file(index, count, path)
            try:
                inspected = self.sync_service.inspect([path], options)
                local_tracks.extend(inspected)
                if on_track is not None:
                    for local_track in inspected:
                        on_track(local_track)
            except Exception as exc:
                file_errors.append(FileCheckError(source_path=path, message=_format_file_error(exc)))
            if should_cancel is not None and should_cancel():
                canceled = True
                break
        overlapping_points, conflicting_points = self._point_relations(local_tracks)
        return PrecheckReport(
            checked_count=len(local_tracks),
            duplicate_groups=self._duplicate_groups(local_tracks),
            overlapping_points=overlapping_points,
            conflicting_points=conflicting_points,
            file_errors=tuple(file_errors),
            canceled=canceled,
        )

    def _duplicate_groups(self, local_tracks: list[LocalTrack]) -> tuple[DuplicateTrackGroup, ...]:
        by_token: dict[str, list[Path]] = defaultdict(list)
        for local_track in local_tracks:
            by_token[local_track.token].append(local_track.track_file.source_path)
        groups = [
            DuplicateTrackGroup(token=token, source_paths=tuple(paths))
            for token, paths in sorted(by_token.items())
            if len(paths) > 1
        ]
        return tuple(groups)

    def _point_relations(
        self,
        local_tracks: list[LocalTrack],
        max_examples: int = 5,
    ) -> tuple[tuple[PointRelationSummary, ...], tuple[PointRelationSummary, ...]]:
        by_timestamp: dict[int, list[_PointRef]] = defaultdict(list)
        for local_track in local_tracks:
            source_path = local_track.track_file.source_path
            for point in local_track.track_file.track.points:
                by_timestamp[_epoch_ms(point)].append(_PointRef(source_path=source_path, point=point))

        overlapping_counts: dict[tuple[Path, Path], int] = defaultdict(int)
        overlapping_examples: dict[tuple[Path, Path], list[PointRelation]] = defaultdict(list)
        conflicting_counts: dict[tuple[Path, Path], int] = defaultdict(int)
        conflicting_examples: dict[tuple[Path, Path], list[PointRelation]] = defaultdict(list)
        for refs in by_timestamp.values():
            if len(refs) < 2:
                continue
            for index, first in enumerate(refs):
                for second in refs[index + 1 :]:
                    if first.source_path == second.source_path:
                        continue
                    first_coord = _coord_key(first.point)
                    second_coord = _coord_key(second.point)
                    pair = tuple(sorted((first.source_path, second.source_path)))
                    if first_coord == second_coord:
                        counts = overlapping_counts
                        examples = overlapping_examples
                    else:
                        counts = conflicting_counts
                        examples = conflicting_examples
                    counts[pair] += 1
                    if len(examples[pair]) < max_examples:
                        examples[pair].append(_relation(first, second))

        return (
            _summarize_relations(overlapping_counts, overlapping_examples),
            _summarize_relations(conflicting_counts, conflicting_examples),
        )


def _epoch_ms(point: TrackPoint) -> int:
    return int(point.timestamp_utc.timestamp() * 1000)


def _coord_key(point: TrackPoint) -> tuple[float, float]:
    return round(point.latitude, 7), round(point.longitude, 7)


def _relation(first: _PointRef, second: _PointRef) -> PointRelation:
    return PointRelation(
        first_source_path=first.source_path,
        second_source_path=second.source_path,
        timestamp_utc=first.point.timestamp_utc,
        first_latitude=first.point.latitude,
        first_longitude=first.point.longitude,
        second_latitude=second.point.latitude,
        second_longitude=second.point.longitude,
    )


def _summarize_relations(
    counts: dict[tuple[Path, Path], int],
    examples: dict[tuple[Path, Path], list[PointRelation]],
) -> tuple[PointRelationSummary, ...]:
    return tuple(
        PointRelationSummary(
            first_source_path=pair[0],
            second_source_path=pair[1],
            count=count,
            examples=tuple(examples[pair]),
        )
        for pair, count in sorted(counts.items(), key=lambda item: (str(item[0][0]), str(item[0][1])))
    )


def _format_file_error(exc: Exception) -> str:
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
