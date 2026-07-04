from __future__ import annotations

import math
from collections import Counter
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

    sample_minutes  # Kept for compatibility with existing callers.
    max_sample_points  # Kept for compatibility with existing callers.

    return _resolve_by_progressive_segments(points, min_population)


def _resolve_by_progressive_segments(points: tuple[TrackPoint, ...], min_population: int) -> str | None:
    start_city = _city_for_point(points[0], min_population)
    end_city = _city_for_point(points[-1], min_population)
    if start_city == end_city:
        return start_city

    sampled_indexes = {0, len(points) - 1}
    votes: Counter[str] = Counter()
    _add_vote(votes, start_city)
    _add_vote(votes, end_city)

    while True:
        winner = _unique_winner(votes)
        if winner:
            return winner

        new_indexes = _segment_midpoint_indexes(sampled_indexes)
        new_indexes = [index for index in new_indexes if index not in sampled_indexes]
        if not new_indexes:
            return start_city

        for index in new_indexes:
            sampled_indexes.add(index)
            _add_vote(votes, _city_for_point(points[index], min_population))


def _segment_midpoint_indexes(sampled_indexes: set[int]) -> list[int]:
    indexes = sorted(sampled_indexes)
    midpoints = []
    for left, right in zip(indexes, indexes[1:]):
        if right - left > 1:
            midpoints.append((left + right) // 2)
    return midpoints


def _unique_winner(votes: Counter[str]) -> str | None:
    if not votes:
        return None
    ranked = votes.most_common()
    if len(ranked) == 1:
        return ranked[0][0]
    if ranked[0][1] > ranked[1][1]:
        return ranked[0][0]
    return None


def _add_vote(votes: Counter[str], city: str | None) -> None:
    if city:
        votes[city] += 1


def _city_for_point(point: TrackPoint, min_population: int) -> str | None:
    return _nearest_city(point.latitude, point.longitude, min_population)


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
