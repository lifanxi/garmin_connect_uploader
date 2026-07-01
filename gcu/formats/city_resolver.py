from __future__ import annotations

import math
from collections import Counter
from datetime import timedelta
from functools import lru_cache
from typing import Any

from gcu.app.models import TrackPoint


def resolve_display_city(
    points: tuple[TrackPoint, ...],
    requested_city: str = "auto",
    sample_minutes: int = 5,
    max_sample_points: int = 25,
    min_population: int = 300_000,
) -> str | None:
    if requested_city != "auto":
        return requested_city or None
    if not points:
        return None

    sample = _middle_sample(points, sample_minutes)
    sample = _thin_sample(sample, max_sample_points)
    votes: Counter[str] = Counter()
    for point in sample:
        city = _nearest_city(point.latitude, point.longitude, min_population)
        if city:
            votes[city] += 1
    if not votes:
        return None
    return votes.most_common(1)[0][0]


def _middle_sample(points: tuple[TrackPoint, ...], sample_minutes: int) -> list[TrackPoint]:
    first = points[0].timestamp_utc
    last = points[-1].timestamp_utc
    middle = first + ((last - first) / 2)
    half_window = timedelta(minutes=sample_minutes / 2)
    start = middle - half_window
    end = middle + half_window
    sample = [point for point in points if start <= point.timestamp_utc <= end]
    if sample:
        return sample
    return [min(points, key=lambda point: abs((point.timestamp_utc - middle).total_seconds()))]


def _thin_sample(points: list[TrackPoint], max_points: int) -> list[TrackPoint]:
    if len(points) <= max_points:
        return points
    step = (len(points) - 1) / (max_points - 1)
    indexes = {round(i * step) for i in range(max_points)}
    return [points[index] for index in sorted(indexes)]


def _nearest_city(latitude: float, longitude: float, min_population: int) -> str | None:
    nearest_name = None
    nearest_distance = float("inf")
    for city in _cities(min_population):
        distance = _haversine_km(
            latitude,
            longitude,
            float(city["latitude"]),
            float(city["longitude"]),
        )
        if distance < nearest_distance:
            nearest_distance = distance
            nearest_name = str(city["name"])
    return nearest_name


@lru_cache(maxsize=16)
def _cities(min_population: int) -> tuple[dict[str, Any], ...]:
    try:
        import geonamescache
    except ImportError as exc:
        raise RuntimeError(
            "Automatic display city detection requires geonamescache. "
            "Install it with: pip install geonamescache"
        ) from exc
    return tuple(
        city
        for city in geonamescache.GeonamesCache().get_cities().values()
        if int(city.get("population") or 0) >= min_population
    )


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(value))
