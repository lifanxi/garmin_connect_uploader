from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from gcu.app.models import TrackFile


@dataclass(frozen=True)
class FormatOptions:
    timezone_name: str = "UTC"
    display_timezone_name: str = "auto"
    display_timezone_fallback: str = "Asia/Shanghai"
    display_city_name: str = "auto"
    display_city_min_population: int = 300_000
    explicit_format: str = "auto"


class TrackReader(Protocol):
    format_id: str

    def can_read(self, path: Path) -> bool:
        ...

    def read(self, path: Path, options: FormatOptions) -> TrackFile:
        ...


def get_reader(path: Path, options: FormatOptions) -> TrackReader:
    from .columbus_csv import ColumbusCsvReader
    from .fit import FitReader
    from .gpx import GpxReader
    from .nmea_rmc import NmeaRmcReader

    readers: tuple[TrackReader, ...] = (ColumbusCsvReader(), NmeaRmcReader(), GpxReader(), FitReader())
    if options.explicit_format != "auto":
        for reader in readers:
            if reader.format_id == options.explicit_format:
                return reader
        raise ValueError(f"Unsupported format: {options.explicit_format}")

    for reader in readers:
        if reader.can_read(path):
            return reader
    raise ValueError(f"Could not detect track format for {path}")
