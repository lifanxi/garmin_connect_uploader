from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from gcu.app.models import RemoteActivity, UploadResult
from gcu.garmin.errors import DuplicateUploadError, UploadConsentRequiredError


class GarminClient:
    def __init__(self, domain: str = "garmin.cn", session_dir: Path | None = None):
        try:
            import garth
            from garth.exc import GarthException, GarthHTTPError
        except ImportError as exc:
            raise RuntimeError("Garmin access requires garth. Install it with: pip install garth") from exc

        self.garth = garth
        self.GarthException = GarthException
        self.GarthHTTPError = GarthHTTPError
        self.domain = domain
        self.session_dir = session_dir or (Path.home() / ".garth")
        self.garth.configure(domain=domain)

    def ensure_session(self, username: str | None = None, password: str | None = None) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.garth.resume(str(self.session_dir))
            getattr(self.garth.client, "username", None)
            return
        except (self.GarthException, FileNotFoundError, OSError):
            pass

        username = username or os.environ.get("GARMIN_USERNAME")
        password = password or os.environ.get("GARMIN_PASSWORD")
        if not username:
            username = input("Garmin username: ")
        if not password:
            from getpass import getpass

            password = getpass("Garmin password: ")
        self.garth.login(username, password)
        self.garth.save(str(self.session_dir))

    def list_activities(self, start_date: date, end_date: date) -> list[RemoteActivity]:
        activities: list[RemoteActivity] = []
        offset = 0
        page_size = 100
        while True:
            page = self.garth.client.connectapi(
                "/activitylist-service/activities/search/activities",
                params={
                    "startDate": start_date.isoformat(),
                    "endDate": end_date.isoformat(),
                    "start": str(offset),
                    "limit": str(page_size),
                },
            )
            if not page:
                break
            activities.extend(_map_activity(item) for item in page)
            if len(page) < page_size:
                break
            offset += page_size
        return activities

    def upload_activity(self, file_path: Path) -> UploadResult:
        with file_path.open("rb") as handle:
            try:
                raw = self.garth.client.upload(handle)
            except self.GarthHTTPError as exc:
                response = getattr(getattr(exc, "error", None), "response", None)
                upload_error = _classify_upload_error(response)
                if upload_error:
                    raise upload_error from exc
                if getattr(response, "status_code", None) == 409:
                    raise DuplicateUploadError("Garmin reports this activity already exists (HTTP 409)") from exc
                raise
        return UploadResult(activity_id=_extract_activity_id(raw), raw=raw)

    def update_activity_name(self, activity_id: int, activity_name: str):
        return self.garth.client.connectapi(
            f"/activity-service/activity/{activity_id}",
            method="PUT",
            json={"activityId": activity_id, "activityName": activity_name},
        )

    def delete_activity(self, activity_id: int):
        return self.garth.client.connectapi(
            f"/activity-service/activity/{activity_id}",
            method="DELETE",
        )

    def ping(self) -> None:
        today = date.today()
        self.list_activities(today - timedelta(days=7), today)

    def wait_for_activity_match(
        self,
        start_date: date,
        predicate,
        max_wait_s: int = 30,
    ) -> RemoteActivity | None:
        delays = [1, 2, 3, 5, 5, 5, 5]
        started = time.time()
        for delay in delays:
            for activity in self.list_activities(start_date, start_date):
                if predicate(activity):
                    return activity
            if time.time() - started + delay > max_wait_s:
                break
            time.sleep(delay)
        return None


def _map_activity(item: dict[str, Any]) -> RemoteActivity:
    return RemoteActivity(
        activity_id=int(item["activityId"]),
        activity_name=item.get("activityName") or "",
        begin_timestamp_ms=_timestamp_to_ms(item.get("beginTimestamp")),
        start_latitude=item.get("startLatitude"),
        start_longitude=item.get("startLongitude"),
        duration_s=_duration_s(item),
        activity_type=_activity_type(item),
        manufacturer=item.get("manufacturer"),
        device_id=_int_or_none(item.get("deviceId")),
    )


def _timestamp_to_ms(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            return int(datetime.fromisoformat(text).astimezone(timezone.utc).timestamp() * 1000)
        except ValueError:
            return None
    return None


def _duration_s(item: dict[str, Any]) -> float | None:
    for key in ("duration", "elapsedDuration", "movingDuration"):
        value = item.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _activity_type(item: dict[str, Any]) -> str | None:
    value = item.get("activityType")
    if isinstance(value, dict):
        return value.get("typeKey") or value.get("typeId")
    if value is not None:
        return str(value)
    return None


def _extract_activity_id(raw: Any) -> int | None:
    if not isinstance(raw, dict):
        return None
    candidates = [
        raw.get("activityId"),
        raw.get("activity", {}).get("activityId") if isinstance(raw.get("activity"), dict) else None,
    ]
    for value in candidates:
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _classify_upload_error(response: Any) -> RuntimeError | None:
    if response is None:
        return None
    try:
        payload = response.json()
    except Exception:
        payload = None

    messages = []
    if isinstance(payload, dict):
        result = payload.get("detailedImportResult")
        if isinstance(result, dict):
            for failure in result.get("failures") or []:
                if not isinstance(failure, dict):
                    continue
                for message in failure.get("messages") or []:
                    if isinstance(message, dict) and message.get("content"):
                        messages.append(str(message["content"]))

    joined = " ".join(messages)
    if "upload consent is not yet granted or revoked" in joined:
        return UploadConsentRequiredError(
            "Garmin account requires upload consent before activities can be uploaded. "
            "Open Garmin Connect or Garmin Express for this account and enable data upload consent."
        )
    if getattr(response, "status_code", None) == 409:
        return DuplicateUploadError("Garmin reports this activity already exists (HTTP 409)")
    return None
