from __future__ import annotations

import hashlib
import re

from gcu.app.models import Track, TrackPoint

TOKEN_RE = re.compile(r"\[gcu:v(?P<version>\d+):(?P<digest>[0-9a-f]{16,64})\]")


def fingerprint_track(track: Track, digest_len: int = 16) -> tuple[str, str]:
    rows = "\n".join(_normalized_point(point) for point in track.points)
    rows_hash = hashlib.sha256(rows.encode("utf-8")).hexdigest()
    metadata = track.metadata
    canonical = "\n".join(
        [
            "gcu-fingerprint-v1",
            f"record_count={metadata.point_count}",
            f"first_timestamp_utc={_epoch_ms(metadata.start_time_utc)}",
            f"last_timestamp_utc={_epoch_ms(metadata.end_time_utc)}",
            f"first_lat={metadata.start_latitude:.7f}",
            f"first_lon={metadata.start_longitude:.7f}",
            f"last_lat={metadata.end_latitude:.7f}",
            f"last_lon={metadata.end_longitude:.7f}",
            f"rows_sha256={rows_hash}",
        ]
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:digest_len]
    return digest, f"[gcu:v1:{digest}]"


def extract_token(text: str | None) -> str | None:
    if not text:
        return None
    match = TOKEN_RE.search(text)
    return match.group(0) if match else None


def append_or_replace_token(name: str, token: str) -> str:
    cleaned = TOKEN_RE.sub("", name).strip()
    return f"{cleaned} {token}".strip()


def _normalized_point(point: TrackPoint) -> str:
    return ",".join(
        [
            str(_epoch_ms(point.timestamp_utc)),
            f"{point.latitude:.7f}",
            f"{point.longitude:.7f}",
            _format_optional(point.altitude_m, 1),
            _format_optional(point.speed_mps, 3),
            _format_optional(point.heading_deg, 0),
        ]
    )


def _format_optional(value: float | None, digits: int) -> str:
    if value is None:
        return ""
    return f"{value:.{digits}f}"


def _epoch_ms(value) -> int:
    return int(value.timestamp() * 1000)
