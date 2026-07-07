from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

from gcu.app.models import TrackPoint


@dataclass(frozen=True)
class DisplayPlace:
    city: str | None = None
    country: str | None = None
    state: str | None = None


def resolve_display_city(
    points: tuple[TrackPoint, ...],
    requested_city: str = "auto",
    sample_minutes: int = 5,
    max_sample_points: int = 25,
    min_population: int = 300_000,
) -> str | None:
    return resolve_display_place(
        points,
        requested_city,
        sample_minutes=sample_minutes,
        max_sample_points=max_sample_points,
        min_population=min_population,
    ).city


def resolve_display_place(
    points: tuple[TrackPoint, ...],
    requested_city: str = "auto",
    sample_minutes: int = 5,
    max_sample_points: int = 25,
    min_population: int = 300_000,
) -> DisplayPlace:
    if requested_city != "auto":
        return DisplayPlace(city=requested_city or None)
    if not points:
        return DisplayPlace()

    sample_minutes  # Kept for compatibility with existing callers.
    max_sample_points  # Kept for compatibility with existing callers.

    return _resolve_by_progressive_segments(points, min_population)


def resolve_city_place(city_name: str, min_population: int = 300_000) -> DisplayPlace:
    normalized = city_name.strip().casefold()
    if not normalized:
        return DisplayPlace()
    matches = [
        city
        for city in _cities(min_population)
        if str(city.get("name") or "").casefold() == normalized
        or normalized in {str(name).casefold() for name in city.get("alternatenames") or ()}
    ]
    if not matches:
        raise ValueError(f"Could not resolve home city: {city_name}")
    city = max(matches, key=lambda item: int(item.get("population") or 0))
    return _place_from_city(city)


def _resolve_by_progressive_segments(points: tuple[TrackPoint, ...], min_population: int) -> DisplayPlace:
    start_place = _place_for_point(points[0], min_population)
    end_place = _place_for_point(points[-1], min_population)
    if start_place == end_place:
        return start_place

    sampled_indexes = {0, len(points) - 1}
    places_by_key: dict[tuple[str, str | None, str | None], DisplayPlace] = {}
    votes: Counter[tuple[str, str | None, str | None]] = Counter()
    _add_vote(votes, places_by_key, start_place)
    _add_vote(votes, places_by_key, end_place)

    while True:
        winner = _unique_winner(votes)
        if winner:
            return places_by_key[winner]

        new_indexes = _segment_midpoint_indexes(sampled_indexes)
        new_indexes = [index for index in new_indexes if index not in sampled_indexes]
        if not new_indexes:
            return start_place

        for index in new_indexes:
            sampled_indexes.add(index)
            _add_vote(votes, places_by_key, _place_for_point(points[index], min_population))


def _segment_midpoint_indexes(sampled_indexes: set[int]) -> list[int]:
    indexes = sorted(sampled_indexes)
    midpoints = []
    for left, right in zip(indexes, indexes[1:]):
        if right - left > 1:
            midpoints.append((left + right) // 2)
    return midpoints


def _unique_winner(
    votes: Counter[tuple[str, str | None, str | None]],
) -> tuple[str, str | None, str | None] | None:
    if not votes:
        return None
    ranked = votes.most_common()
    if len(ranked) == 1:
        return ranked[0][0]
    if ranked[0][1] > ranked[1][1]:
        return ranked[0][0]
    return None


def _add_vote(
    votes: Counter[tuple[str, str | None, str | None]],
    places_by_key: dict[tuple[str, str | None, str | None], DisplayPlace],
    place: DisplayPlace,
) -> None:
    if not place.city:
        return
    key = (place.city, place.country, place.state)
    places_by_key[key] = place
    votes[key] += 1


def _place_for_point(point: TrackPoint, min_population: int) -> DisplayPlace:
    return _nearest_place(point.latitude, point.longitude, min_population)


def _nearest_place(latitude: float, longitude: float, min_population: int) -> DisplayPlace:
    nearest_city = None
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
            nearest_city = city
    if nearest_city is None:
        return DisplayPlace()
    return _place_from_city(nearest_city)


def _place_from_city(city: dict[str, Any]) -> DisplayPlace:
    country_code = str(city.get("countrycode") or "")
    admin1_code = str(city.get("admin1code") or "")
    return DisplayPlace(
        city=str(city["name"]),
        country=_country_name(country_code),
        state=_admin1_name(country_code, admin1_code),
    )


def _country_name(country_code: str) -> str | None:
    return _countries().get(country_code, {}).get("name")


def _admin1_name(country_code: str, admin1_code: str) -> str | None:
    if not country_code or not admin1_code:
        return None
    return _admin1_names().get(f"{country_code}.{admin1_code}")


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


@lru_cache(maxsize=1)
def _countries() -> dict[str, dict[str, Any]]:
    try:
        import geonamescache
    except ImportError as exc:
        raise RuntimeError(
            "Automatic display country detection requires geonamescache. "
            "Install it with: pip install geonamescache"
        ) from exc
    return geonamescache.GeonamesCache().get_countries()


@lru_cache(maxsize=1)
def _admin1_names() -> dict[str, str]:
    data_path = resources.files("gcu.formats").joinpath("data", "admin1CodesASCII.txt")
    names: dict[str, str] = {}
    with data_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3:
                names[parts[0]] = parts[2] or parts[1]
    return names


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
