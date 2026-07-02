from __future__ import annotations

import base64
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from requests import Response, Session


ENABLE_ENV = "GCU_GARMIN_VERBOSE_HTTP"
LOG_PATH_ENV = "GCU_GARMIN_VERBOSE_HTTP_LOG"
DEFAULT_LOG_PATH = "garmin-connect-http.log"

_LOCK = threading.Lock()


def configure_verbose_http_logging(session: Session) -> None:
    if not _env_enabled(os.environ.get(ENABLE_ENV)):
        return
    log_path = Path(os.environ.get(LOG_PATH_ENV) or _default_log_path())
    hook = _build_response_hook(log_path)
    hooks = session.hooks.setdefault("response", [])
    if not any(getattr(item, "_gcu_verbose_http_log_path", None) == str(log_path) for item in hooks):
        setattr(hook, "_gcu_verbose_http_log_path", str(log_path))
        hooks.append(hook)


def _build_response_hook(log_path: Path):
    def hook(response: Response, *args, **kwargs) -> Response:
        try:
            _append_exchange(log_path, response)
        except OSError as exc:
            _report_log_failure(log_path, exc)
        return response

    return hook


def _append_exchange(log_path: Path, response: Response) -> None:
    request = response.request
    lines = [
        "",
        "=" * 96,
        f"{datetime.now().isoformat(timespec='seconds')} Garmin Connect HTTP exchange",
        "-" * 96,
        "REQUEST",
        f"{request.method} {request.url}",
        "Headers:",
        _format_headers(request.headers),
        "Body:",
        _format_body(request.body),
        "-" * 96,
        "RESPONSE",
        f"HTTP {response.status_code} {response.reason}",
        f"URL: {response.url}",
        "Headers:",
        _format_headers(response.headers),
        "Body:",
        _format_body(response.content),
        "=" * 96,
        "",
    ]
    with _LOCK:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines))


def _format_headers(headers: Any) -> str:
    if not headers:
        return "  <empty>"
    return "\n".join(f"  {key}: {value}" for key, value in headers.items())


def _format_body(body: Any) -> str:
    if body is None:
        return "  <empty>"
    if isinstance(body, str):
        return body
    if isinstance(body, bytes):
        return _format_bytes(body)
    try:
        data = bytes(body)
    except Exception:
        return repr(body)
    return _format_bytes(data)


def _format_bytes(data: bytes) -> str:
    if not data:
        return "  <empty>"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        encoded = base64.b64encode(data).decode("ascii")
        return f"<base64 length={len(data)}>\n{encoded}"
    if _mostly_text(text):
        return text
    encoded = base64.b64encode(data).decode("ascii")
    return f"<base64 length={len(data)}>\n{encoded}"


def _mostly_text(text: str) -> bool:
    if not text:
        return True
    control_count = sum(1 for char in text if ord(char) < 32 and char not in "\r\n\t")
    return control_count / len(text) < 0.01


def _env_enabled(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on", "verbose", "debug"}


def _default_log_path() -> str:
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA")
        if root:
            return str(Path(root) / "GarminConnectUploader" / DEFAULT_LOG_PATH)
    return DEFAULT_LOG_PATH


def _report_log_failure(log_path: Path, exc: OSError) -> None:
    message = f"Could not write Garmin verbose HTTP log to {log_path}: {exc}\n"
    try:
        sys.stderr.write(message)
    except Exception:
        pass
