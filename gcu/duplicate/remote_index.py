from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from gcu.app.models import RemoteActivity
from gcu.duplicate.fingerprint import extract_token
from gcu.garmin.signature import is_gcu_activity


@dataclass
class RemoteActivityIndex:
    by_token: dict[str, RemoteActivity] = field(default_factory=dict)
    by_date: dict[date, list[RemoteActivity]] = field(default_factory=dict)
    activities: list[RemoteActivity] = field(default_factory=list)

    @classmethod
    def build(cls, activities: list[RemoteActivity]) -> "RemoteActivityIndex":
        index = cls(activities=activities)
        for activity in activities:
            token = extract_token(activity.activity_name)
            if token and is_gcu_activity(activity):
                index.by_token[token] = activity
            if activity.begin_timestamp_ms is not None:
                activity_date = _date_from_epoch_ms(activity.begin_timestamp_ms)
                index.by_date.setdefault(activity_date, []).append(activity)
        return index


def _date_from_epoch_ms(value: int) -> date:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date()
