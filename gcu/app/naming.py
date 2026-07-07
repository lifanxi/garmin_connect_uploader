from __future__ import annotations

import re
from zoneinfo import ZoneInfo

from gcu.app.models import Track
from gcu.duplicate.fingerprint import append_or_replace_token


def planned_activity_name(
    track: Track,
    token: str,
    template: str | None = None,
    home_country: str | None = None,
    home_state: str | None = None,
) -> str:
    metadata = track.metadata
    display_tz = ZoneInfo(metadata.display_timezone)
    if template:
        values = dict(
            date=metadata.start_time_utc.astimezone(display_tz).strftime("%b %d"),
            duration=_format_duration(metadata.duration_s),
            start=metadata.start_time_utc.isoformat(),
            end=metadata.end_time_utc.isoformat(),
            points=metadata.point_count,
            city=metadata.display_city or "",
            country=metadata.display_country or "",
            state=metadata.display_state or "",
        )
        base = _format_template(template, values, home_country=home_country, home_state=home_state)
    else:
        base = metadata.display_name
    return append_or_replace_token(base, token)


def _format_duration(duration_s: float) -> str:
    total_minutes = max(0, int(duration_s // 60))
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def _format_template(
    template: str,
    values: dict[str, object],
    home_country: str | None = None,
    home_state: str | None = None,
) -> str:
    optional_seen = False
    optional_matches = list(re.finditer(r"\[([^\[\]]+)\]", template))
    optional_fields = _template_fields(" ".join(match.group(1) for match in optional_matches))
    required_text = re.sub(r"\[([^\[\]]+)\]", " ", template)
    required_fields = _template_fields(required_text)
    country = str(values.get("country") or "")
    state = str(values.get("state") or "")
    city = str(values.get("city") or "")
    same_country = bool(country and home_country and country == home_country)
    same_state = bool(state and home_state and state == home_state and (not home_country or country == home_country))
    same_city_state = bool(city and state and city == state)
    optional_fields_to_drop: set[str] = set()
    required_fields_to_drop: set[str] = set()

    if same_city_state:
        state_only_optional = "state" in optional_fields and "city" not in optional_fields and "city" in required_fields
        city_only_optional = "city" in optional_fields and "state" not in optional_fields and "state" in required_fields
        if state_only_optional:
            optional_fields_to_drop.add("state")
        elif city_only_optional:
            optional_fields_to_drop.add("city")
        else:
            optional_fields_to_drop.add("state")
            required_fields_to_drop.add("state")

    def replace_optional(match: re.Match[str]) -> str:
        nonlocal optional_seen
        optional_seen = True
        block = match.group(1)
        fields = _template_fields(block)
        if same_state and fields & {"country", "state"}:
            return ""
        if same_country and "country" in fields:
            return ""
        block_values = values | {field: "" for field in optional_fields_to_drop}
        formatted = block.format(**block_values)
        return formatted if formatted.strip() else ""

    render_values = values | {field: "" for field in required_fields_to_drop}
    rendered = re.sub(r"\[([^\[\]]+)\]", replace_optional, template)
    rendered = rendered.format(**render_values)
    if optional_seen or same_city_state:
        rendered = re.sub(r"\s{2,}", " ", rendered).strip()
    return rendered


def _template_fields(text: str) -> set[str]:
    return set(re.findall(r"{([a-zA-Z_][a-zA-Z0-9_]*)}", text))
