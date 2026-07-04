from __future__ import annotations

import json
import re
import sys
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from PySide6.QtCore import QLocale, QThreadPool, QTimer, Qt
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gcu.app.models import (
    AuthenticatedUser,
    FileCheckError,
    LocalTrack,
    PrecheckReport,
    PurgeSummary,
    SyncDecision,
)
from gcu.app.precheck_service import PrecheckService
from gcu.app.sync_service import SyncOptions, SyncService
from gcu.formats.base import FormatOptions
from gcu.garmin import GarminClient
from gcu.garmin.client import ACCOUNT_HINT_FILE
from gcu.gui.workers import TaskWorker


@dataclass(frozen=True)
class GarminSettings:
    domain: str
    session_dir: Path
    username: str
    password: str


@dataclass(frozen=True)
class InspectBundle:
    report: PrecheckReport
    tracks: list[LocalTrack]


class SortableTableItem(QTableWidgetItem):
    def __lt__(self, other) -> bool:
        left = self.data(Qt.UserRole + 2)
        right = other.data(Qt.UserRole + 2)
        if left is not None or right is not None:
            if left is None:
                return False
            if right is None:
                return True
            return left < right
        return self.text().casefold() < other.text().casefold()


class ErrorDialog(QDialog):
    def __init__(self, parent: QWidget, title: str, summary: str, details: str, tr) -> None:
        super().__init__(parent)
        self._tr = tr
        self.setWindowTitle(title)
        self.setModal(True)

        layout = QVBoxLayout(self)
        summary_label = QLabel(summary)
        summary_label.setWordWrap(True)
        layout.addWidget(summary_label)

        self.details_button = QPushButton(self._tr("error_details"))
        self.details_button.setCheckable(True)
        self.details_button.clicked.connect(self._toggle_details)
        layout.addWidget(self.details_button, 0, Qt.AlignLeft)

        self.details_output = QPlainTextEdit()
        self.details_output.setReadOnly(True)
        self.details_output.setPlainText(f"{self._tr('error_details_intro')}\n\n{details.strip()}")
        self.details_output.setMinimumWidth(720)
        self.details_output.setMinimumHeight(260)
        self.details_output.setVisible(False)
        layout.addWidget(self.details_output)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.resize(460, self.minimumSizeHint().height())

    def _toggle_details(self, checked: bool) -> None:
        self.details_output.setVisible(checked)
        self.details_button.setText(self._tr("hide_error_details") if checked else self._tr("error_details"))
        if checked:
            self.resize(max(self.width(), 780), max(self.height(), 420))
        else:
            self.resize(460, self.minimumSizeHint().height())


SESSION_DIR = Path(".garth_session")
SESSION_META_FILE = ACCOUNT_HINT_FILE
LEGACY_SESSION_META_FILE = "gcu_session.json"
PURGE_CHUNK_DAYS = 366
FILE_COLUMN = 0
START_UTC_COLUMN = 1
END_UTC_COLUMN = 2
CITY_COLUMN = 3
POINTS_COLUMN = 4
PLAN_COLUMN = 5
NAME_COLUMN = 6
FORMAT_COLUMN = 7
MESSAGE_COLUMN = 8
ACTIVITY_COLUMN = 9
TOKEN_COLUMN = 10
PLAN_STATUS_ROLE = Qt.UserRole + 1
RUNNABLE_PLAN_STATUSES = {"upload", "skip-legacy-match", "backfilled-token"}
SUCCESSFUL_TASK_STATUSES = {"upload", "skip-token", "backfilled-token", "upload-conflict"}
COMPLETED_PLAN_STATUSES = {"skip-token", "backfilled-token", "upload-conflict", "completed"}
SUPPORTED_DOMAINS = {"garmin.com", "garmin.cn"}
APP_ICON_PATH = Path("assets/icons/gcu-icon.png")
APP_USER_MODEL_ID = "LiFanxi.GarminConnectUploader"


TRANSLATIONS = {
    "en": {
        "title": "Garmin Connect Uploader",
        "nav_login": "Login",
        "nav_files": "Files",
        "nav_purge": "Purge",
        "status_log": "Status",
        "ready": "Ready",
        "domain": "Domain",
        "global_site": "Global",
        "china_site": "China",
        "session_dir": "Session dir",
        "username": "Username",
        "password": "Password",
        "login": "Login",
        "logout": "Logout",
        "logout_complete": "Logged out. Cached Garmin session was removed.",
        "add_files": "Add Files",
        "add_folder": "Add Folder",
        "remove": "Remove",
        "clear_completed": "Clear Completed",
        "clear": "Clear",
        "inspect": "Inspect",
        "stop_inspect": "Stop Inspect",
        "run": "Run",
        "stop_run": "Stop Run",
        "stopping_run": "Stopping after current file",
        "run_stopped": "Run stopped after current file.",
        "file": "File",
        "format": "Format",
        "points": "Points",
        "start_utc": "Start UTC",
        "end_utc": "End UTC",
        "city": "City",
        "token": "Token",
        "plan": "Status",
        "activity": "Activity",
        "name": "Name",
        "message": "Message",
        "warnings": "Warnings",
        "start": "Start",
        "end": "End",
        "duration": "Duration",
        "delete_matched": "Clean Uploaded Tracks",
        "recursive_add": "Recursive add",
        "add_folder_recursive": "Add subfolders recursively?",
        "add_track_files": "Add track files",
        "track_files_filter": "Track files (*.CSV *.csv *.gpx *.GPX *.txt *.nmea *.log);;All files (*)",
        "add_folder_title": "Add folder",
        "checking_files": "Checking files",
        "stopping_check": "Stopping check",
        "check_stopped": "Check stopped",
        "precheck_local": "Running local pre-check for {count} files",
        "precheck_file": "Pre-checking file {index}/{count}: {name}",
        "precheck_ok": "Local pre-check passed",
        "precheck_issue_summary": "Local pre-check found duplicate, overlap, or conflict issues",
        "plan_file_error": "File Error",
        "plan_duplicate": "Duplicate",
        "plan_overlap": "Overlap",
        "plan_conflict": "Conflict",
        "precheck_parse_error": "Parse error: {message}",
        "precheck_duplicate_issue": "Duplicate with {count} other tracks",
        "precheck_overlap_issue": "Overlaps {count} point pair(s) with {other}",
        "precheck_conflict_issue": "Conflicts {count} point pair(s) with {other}",
        "inspect_file": "Inspecting file {index}/{count}: {name}",
        "inspect_file_done": "Inspected file {index}/{count}: {name}",
        "using_cached_track": "Using cached parse result: {name}",
        "connect_garmin": "Connecting to Garmin Connect",
        "query_remote": "Querying remote activities and planning tasks",
        "query_remote_sync": "Querying remote activities and running sync tasks",
        "sync_file_queued": "Queued sync task {index}/{count}: {name}",
        "no_runnable_plans": "No runnable plans. Inspect files first, then run rows marked Upload or Backfill Token.",
        "synchronizing": "Synchronizing tracks",
        "logging_in": "Logging in",
        "checking_session": "Checking session",
        "no_valid_session": "No valid login session. Enter username and password to log in to Garmin Connect.",
        "previewing_purge": "Previewing purge",
        "deleting_signed": "Deleting signed activities",
        "confirm_purge": "Confirm purge",
        "confirm_purge_text": "The following uploaded tracks will be deleted. Type DELETE to continue.",
        "confirm_text": "Confirm",
        "no_purge_matches": "No uploaded tracks matched the cleanup criteria.",
        "login_complete": "Login complete.",
        "session_usable": "Session is usable.",
        "precheck_issues": "Pre-check found issues",
        "continue_dry_run": "Continue to task planning?",
        "inspect_stopped": "Inspect stopped after pre-check",
        "planning_sync": "Planning sync",
        "waiting_plan": "Waiting for sync plan",
        "completed_plan_summary": "Completed plans for {count} tracks: upload {upload}, skip {skip}, backfill Token {backfill}, failed {failed}, other {other}",
        "completed_task_summary": "Completed {count} track tasks: upload {upload}, skip {skip}, backfill Token {backfill}, failed {failed}, other {other}",
        "plan_upload": "Upload",
        "plan_skip": "Skip",
        "plan_backfill_token": "Backfill Token",
        "plan_upload_conflict": "Resolve upload conflict",
        "plan_ambiguous": "Needs review",
        "plan_failed": "Failed",
        "plan_queued": "Queued",
        "plan_uploading": "Uploading",
        "plan_completed": "Completed",
        "sync_progress_planning": "Planning task: {name}",
        "sync_progress_write_fit": "Rendering FIT: {name}",
        "sync_progress_upload": "Uploading: {name}",
        "sync_progress_wait_uploaded": "Waiting for Garmin activity after upload: {name} ({wait_s}s max)",
        "sync_progress_update_name": "Updating activity name: {name}",
        "sync_progress_backfill_token": "Backfilling Token: {name}",
        "sync_progress_resolve_conflict": "Resolving upload conflict: {name}",
        "decision_duplicate_local": "Duplicate local track in this batch",
        "decision_token_added": "Token added to existing activity",
        "decision_upload_unavailable": "Uploaded, but Garmin activity was unavailable after {wait_s}s; not tagged",
        "decision_uploaded_tagged": "Uploaded and tagged",
        "decision_token_exists": "Token already exists",
        "decision_token_backfilled": "Token backfilled",
        "decision_would_backfill": "Would backfill Token",
        "decision_multiple_legacy": "Multiple matching remote activities",
        "decision_no_matching_remote": "No matching remote activity",
        "decision_signed_gcu": "Signed GCU activity",
        "decision_signed_deleted": "Signed GCU activity deleted",
        "decision_remote_token_match": "Remote Token match",
        "decision_legacy_has_token": "Matched remote activity already has Token",
        "decision_legacy_match": "Matched remote activity without Token",
        "decision_no_duplicate": "No duplicate found",
        "decision_duplicate_added_token": "Garmin reported duplicate; Token added to matched activity",
        "decision_duplicate_multiple": "Garmin reported duplicate; multiple remote matches",
        "decision_duplicate_no_unique": "Garmin reported duplicate; no unique remote match",
        "inspected_files": "Inspected {count} files",
        "clear_completed_done": "Cleared {count} completed tasks.",
        "clear_completed_none": "No completed tasks to clear.",
        "no_files_title": "No files",
        "no_files": "Add one or more track files first.",
        "failed": "Failed",
        "error": "Error",
        "error_details": "Details",
        "hide_error_details": "Hide details",
        "error_default_summary": "The operation failed. Please check your network connection and Garmin account status, then try again.",
        "error_login_summary": "Login failed. Check the username, password, domain, and Garmin account status, then try again.",
        "error_auth_summary": "Garmin rejected this request. Please log out, log in again, and retry.",
        "error_network_summary": "Could not reach Garmin Connect. Check the network connection, then try again.",
        "error_server_summary": "Garmin Connect returned an error. Please retry later, or log in again if the problem continues.",
        "error_details_intro": "Technical details",
        "task_running": "Task running",
        "task_running_text": "Wait for the current task to finish before closing.",
        "checked_files": "Checked files",
        "duplicate_groups": "Duplicate groups",
        "overlapping_pairs": "Overlapping point pairs",
        "conflicting_pairs": "Conflicting point pairs",
        "file_errors": "File errors",
        "duplicate_tracks": "Duplicate tracks",
        "overlapping_points": "Overlapping points",
        "conflicting_points": "Conflicting points",
        "date_range": "Date range",
        "scanned": "Scanned",
        "matched": "Matched",
        "skipped_unsigned": "Skipped unsigned",
        "would_delete": "would delete",
        "deleted": "deleted",
        "first": "first",
        "second": "second",
        "manufacturer": "manufacturer",
        "device_id": "deviceId",
    },
    "zh": {
        "title": "Garmin Connect 上传工具",
        "nav_login": "登录",
        "nav_files": "文件",
        "nav_purge": "清理",
        "status_log": "状态",
        "ready": "就绪",
        "domain": "站点",
        "global_site": "国际站",
        "china_site": "中国站",
        "session_dir": "凭证目录",
        "username": "用户名",
        "password": "密码",
        "login": "登录",
        "logout": "退出登录",
        "logout_complete": "已退出登录，并清除了本地 Garmin 登录缓存。",
        "add_files": "添加文件",
        "add_folder": "添加文件夹",
        "remove": "移除",
        "clear_completed": "清理已完成任务",
        "clear": "清空",
        "inspect": "检查",
        "stop_inspect": "中止检查",
        "run": "运行",
        "stop_run": "中断运行",
        "stopping_run": "当前文件完成后将中断运行",
        "run_stopped": "运行已在当前文件完成后中断。",
        "file": "文件",
        "format": "格式",
        "points": "点数",
        "start_utc": "开始 UTC",
        "end_utc": "结束 UTC",
        "city": "城市",
        "token": "Token",
        "plan": "状态",
        "activity": "活动",
        "name": "名称",
        "message": "消息",
        "warnings": "警告",
        "start": "开始",
        "end": "结束",
        "duration": "时长",
        "delete_matched": "清理已上传轨迹",
        "recursive_add": "递归添加",
        "add_folder_recursive": "是否递归添加子文件夹中的轨迹文件？",
        "add_track_files": "添加轨迹文件",
        "track_files_filter": "轨迹文件 (*.CSV *.csv *.gpx *.GPX *.txt *.nmea *.log);;所有文件 (*)",
        "add_folder_title": "添加文件夹",
        "checking_files": "正在检查文件",
        "stopping_check": "正在中止检查",
        "check_stopped": "检查已中止",
        "precheck_local": "正在对 {count} 个文件做本地合法性检查",
        "precheck_file": "正在做本地合法性检查 {index}/{count}: {name}",
        "precheck_ok": "本地合法性检查通过",
        "precheck_issue_summary": "本地合法性检查发现重复、重叠或冲突问题",
        "plan_file_error": "文件错误",
        "plan_duplicate": "重复",
        "plan_overlap": "重叠",
        "plan_conflict": "冲突",
        "precheck_parse_error": "解析错误: {message}",
        "precheck_duplicate_issue": "与 {count} 个其他轨迹重复",
        "precheck_overlap_issue": "与 {other} 重叠 {count} 个点对",
        "precheck_conflict_issue": "与 {other} 冲突 {count} 个点对",
        "inspect_file": "正在处理文件 {index}/{count}: {name}",
        "inspect_file_done": "文件处理完成 {index}/{count}: {name}",
        "using_cached_track": "使用已检查结果: {name}",
        "connect_garmin": "正在连接 Garmin Connect",
        "query_remote": "正在查询远端活动并生成任务计划",
        "query_remote_sync": "正在查询远端活动并执行同步任务",
        "sync_file_queued": "已加入同步队列 {index}/{count}: {name}",
        "no_runnable_plans": "没有可运行的任务计划。请先检查文件，然后运行标记为上传或补填Token的行。",
        "synchronizing": "正在同步轨迹",
        "logging_in": "正在登录",
        "checking_session": "正在检查登录",
        "no_valid_session": "无有效登录凭证，请输入用户名密码登录 Garmin Connect",
        "previewing_purge": "正在预览清理",
        "deleting_signed": "正在删除签名活动",
        "confirm_purge": "确认清理",
        "confirm_purge_text": "以下已上传轨迹将被删除。输入 DELETE 以继续。",
        "confirm_text": "确认",
        "no_purge_matches": "没有发现符合清理条件的已上传轨迹。",
        "login_complete": "登录完成。",
        "session_usable": "登录凭证可用。",
        "precheck_issues": "预检查发现问题",
        "continue_dry_run": "是否继续生成任务计划？",
        "inspect_stopped": "预检查后已停止检查",
        "planning_sync": "正在生成同步计划",
        "waiting_plan": "等待任务计划",
        "completed_plan_summary": "已完成 {count} 个轨迹任务计划，其中上传 {upload} 条，跳过 {skip} 条，补填Token {backfill} 条，失败 {failed} 条，其它 {other} 条",
        "completed_task_summary": "已完成 {count} 个轨迹任务，其中上传 {upload} 条，跳过 {skip} 条，补填Token {backfill} 条，失败 {failed} 条，其它 {other} 条",
        "plan_upload": "上传",
        "plan_skip": "跳过",
        "plan_backfill_token": "补填Token",
        "plan_upload_conflict": "处理上传冲突",
        "plan_ambiguous": "需人工确认",
        "plan_failed": "失败",
        "plan_queued": "排队中",
        "plan_uploading": "上传中",
        "plan_completed": "完成",
        "sync_progress_planning": "正在判断处理方式: {name}",
        "sync_progress_write_fit": "正在生成 FIT: {name}",
        "sync_progress_upload": "正在上传: {name}",
        "sync_progress_wait_uploaded": "正在等待 Garmin 生成活动: {name}（最多 {wait_s} 秒）",
        "sync_progress_update_name": "正在更新活动名称: {name}",
        "sync_progress_backfill_token": "正在补填Token: {name}",
        "sync_progress_resolve_conflict": "正在处理上传冲突: {name}",
        "decision_duplicate_local": "本批次中有相同轨迹",
        "decision_token_added": "已给已有活动补填 Token",
        "decision_upload_unavailable": "已上传，但 {wait_s} 秒后仍无法获取 Garmin 活动，未完成标记",
        "decision_uploaded_tagged": "已上传并完成标记",
        "decision_token_exists": "远端已存在相同 Token",
        "decision_token_backfilled": "已补填 Token",
        "decision_would_backfill": "将补填 Token",
        "decision_multiple_legacy": "匹配到多个远端活动",
        "decision_no_matching_remote": "没有匹配的远端活动",
        "decision_signed_gcu": "已识别为本工具上传的活动",
        "decision_signed_deleted": "已删除本工具上传的活动",
        "decision_remote_token_match": "远端 Token 匹配",
        "decision_legacy_has_token": "匹配的远端活动已包含 Token",
        "decision_legacy_match": "匹配到未标记 Token 的远端活动",
        "decision_no_duplicate": "未发现重复",
        "decision_duplicate_added_token": "Garmin 返回重复，已给匹配活动补填 Token",
        "decision_duplicate_multiple": "Garmin 返回重复，匹配到多个远端活动",
        "decision_duplicate_no_unique": "Garmin 返回重复，但没有唯一匹配的远端活动",
        "inspected_files": "已检查 {count} 个文件",
        "clear_completed_done": "已清理 {count} 个已完成任务。",
        "clear_completed_none": "没有可清理的已完成任务。",
        "no_files_title": "没有文件",
        "no_files": "请先添加一个或多个轨迹文件。",
        "failed": "失败",
        "error": "错误",
        "error_details": "详细日志",
        "hide_error_details": "收起日志",
        "error_default_summary": "操作失败。请检查网络连接和 Garmin 账号状态后重试。",
        "error_login_summary": "登录失败。请检查用户名、密码、站点和 Garmin 账号状态后重试。",
        "error_auth_summary": "Garmin 拒绝了本次请求。请退出登录后重新登录，再重试。",
        "error_network_summary": "无法连接 Garmin Connect。请检查网络连接后重试。",
        "error_server_summary": "Garmin Connect 返回了错误。请稍后重试；如果持续失败，请重新登录。",
        "error_details_intro": "技术细节",
        "task_running": "任务运行中",
        "task_running_text": "请等待当前任务结束后再关闭。",
        "checked_files": "已检查文件数",
        "duplicate_groups": "重复轨迹组",
        "overlapping_pairs": "重叠点文件对",
        "conflicting_pairs": "冲突点文件对",
        "file_errors": "文件解析错误",
        "duplicate_tracks": "重复轨迹",
        "overlapping_points": "重叠点",
        "conflicting_points": "冲突点",
        "date_range": "日期范围",
        "scanned": "已扫描",
        "matched": "已匹配",
        "skipped_unsigned": "跳过非本工具活动",
        "would_delete": "将删除",
        "deleted": "已删除",
        "first": "第一条",
        "second": "第二条",
        "manufacturer": "厂商",
        "device_id": "设备 ID",
    },
}


def _language() -> str:
    name = QLocale.system().name()
    return "zh" if name.startswith(("zh_CN", "zh_SG", "zh_Hans")) else "en"


def _resource_path(relative_path: Path) -> Path:
    pyinstaller_base = getattr(sys, "_MEIPASS", None)
    if pyinstaller_base:
        candidate = Path(pyinstaller_base) / relative_path
        if candidate.exists():
            return candidate
    source_base = Path(__file__).resolve().parents[2]
    candidate = source_base / relative_path
    if candidate.exists():
        return candidate
    return Path(sys.executable).resolve().parent / relative_path


def _set_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def _apply_application_style(app: QApplication) -> None:
    app.setStyleSheet(
        """
        QGroupBox#sectionGroup {
            background-color: #fbfcfd;
            border: 1px solid #cfd7df;
            border-radius: 8px;
            margin-top: 14px;
            padding: 14px 10px 10px 10px;
            font-weight: 600;
        }

        QGroupBox#sectionGroup::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            top: 0px;
            padding: 1px 9px;
            color: #1f2933;
            background-color: #ffffff;
            border: 1px solid #cfd7df;
            border-radius: 7px;
        }

        QTableWidget, QPlainTextEdit {
            background-color: #ffffff;
            border: 1px solid #b8c0c8;
            border-radius: 4px;
            selection-background-color: #d7ebff;
            selection-color: #111827;
        }

        QHeaderView::section {
            background-color: #f1f4f7;
            border: 0px;
            border-right: 1px solid #c3cbd3;
            border-bottom: 1px solid #b8c0c8;
            padding: 5px 6px;
            font-weight: 600;
        }

        QProgressBar {
            border: 0px;
            border-radius: 3px;
            background-color: #f4f6f8;
            max-height: 6px;
        }

        QProgressBar::chunk {
            border-radius: 3px;
            background-color: #1677ff;
        }
        """
    )


def _normalize_domain(domain: str | None) -> str | None:
    return domain if domain in SUPPORTED_DOMAINS else None


def _session_meta_path(session_dir: Path = SESSION_DIR) -> Path:
    return session_dir / SESSION_META_FILE


def _legacy_session_meta_path(session_dir: Path = SESSION_DIR) -> Path:
    return session_dir / LEGACY_SESSION_META_FILE


def _load_session_meta(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_saved_domain(session_dir: Path = SESSION_DIR) -> str | None:
    domain = _normalize_domain(_load_session_meta(_session_meta_path(session_dir)).get("domain"))
    if domain:
        return domain
    return _normalize_domain(_load_session_meta(_legacy_session_meta_path(session_dir)).get("domain"))


def _save_saved_domain(domain: str, session_dir: Path = SESSION_DIR) -> None:
    domain = _normalize_domain(domain)
    if not domain:
        return
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
        payload = _load_session_meta(_session_meta_path(session_dir))
        payload["domain"] = domain
        _session_meta_path(session_dir).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        _legacy_session_meta_path(session_dir).unlink(missing_ok=True)
    except OSError:
        pass


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.lang = _language()
        self.texts = TRANSLATIONS[self.lang]
        self.setWindowTitle(self.tr("title"))
        self.thread_pool = QThreadPool.globalInstance()
        self.files: list[Path] = []
        self.local_tracks: list[LocalTrack] = []
        self._local_track_cache: dict[Path, tuple[int, int, FormatOptions, LocalTrack]] = {}
        self._active_tasks = 0
        self._workers: list[TaskWorker] = []
        self._authenticated = False
        self._sort_after_sync_done = False
        self._inspect_task_running = False
        self._inspect_cancel_requested = False
        self._sync_task_running = False
        self._sync_cancel_requested = False
        self._queued_plan_snapshot: dict[Path, tuple[str, str | None]] = {}

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        self.login_page = self._build_login_page()
        self._restore_saved_domain()
        self.files_page = self._build_files_page()
        self.purge_page = self._build_purge_page()
        self.status_page = self._build_status_page()
        layout.addWidget(self.login_page, 0)
        layout.addWidget(self.files_page, 1)
        layout.addWidget(self.purge_page, 0)
        layout.addWidget(self.status_page, 0)
        self.setCentralWidget(root)

        self.status = QProgressBar()
        self.status.setRange(0, 1)
        self.status.setValue(1)
        self.status.setTextVisible(False)
        self.status.setFixedHeight(8)
        self.statusBar().addPermanentWidget(self.status, 1)
        self.statusBar().showMessage(self.tr("ready"))
        self._refresh_action_state()
        self._resize_to_initial_size()
        QTimer.singleShot(0, self._auto_check_session)

    def tr(self, key: str, **kwargs) -> str:
        text = self.texts.get(key, TRANSLATIONS["en"].get(key, key))
        return text.format(**kwargs) if kwargs else text

    def _build_login_page(self) -> QWidget:
        page = QGroupBox(self.tr("nav_login"))
        page.setObjectName("sectionGroup")
        layout = QHBoxLayout(page)

        self.global_domain_radio = QRadioButton(self.tr("global_site"))
        self.china_domain_radio = QRadioButton(self.tr("china_site"))
        self.domain_group = QButtonGroup(self)
        self.domain_group.addButton(self.global_domain_radio)
        self.domain_group.addButton(self.china_domain_radio)
        if self.lang == "zh":
            self.china_domain_radio.setChecked(True)
        else:
            self.global_domain_radio.setChecked(True)
        domain_layout = QHBoxLayout()
        domain_layout.addWidget(self.global_domain_radio)
        domain_layout.addWidget(self.china_domain_radio)
        domain_layout.addStretch(1)
        domain_widget = QWidget()
        domain_widget.setLayout(domain_layout)
        self.username_input = QLineEdit()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.username_input.setMinimumWidth(220)
        self.password_input.setMinimumWidth(180)

        self.login_button = QPushButton(self.tr("login"))
        layout.addWidget(QLabel(self.tr("domain")))
        layout.addWidget(domain_widget)
        layout.addWidget(QLabel(self.tr("username")))
        layout.addWidget(self.username_input, 1)
        layout.addWidget(QLabel(self.tr("password")))
        layout.addWidget(self.password_input)
        layout.addWidget(self.login_button)

        self.login_button.clicked.connect(self._login_or_logout)
        self.password_input.returnPressed.connect(self._login_from_password_return)
        return page

    def _build_files_page(self) -> QWidget:
        page = QGroupBox(self.tr("nav_files"))
        page.setObjectName("sectionGroup")
        layout = QVBoxLayout(page)

        toolbar = QHBoxLayout()
        self.add_files_button = QPushButton(self.tr("add_files"))
        self.add_folder_button = QPushButton(self.tr("add_folder"))
        self.remove_files_button = QPushButton(self.tr("remove"))
        self.clear_files_button = QPushButton(self.tr("clear"))
        for button in (
            self.add_files_button,
            self.add_folder_button,
            self.remove_files_button,
            self.clear_files_button,
        ):
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.files_table = QTableWidget(0, 11)
        self.files_table.setHorizontalHeaderLabels(
            [
                self.tr("file"),
                self.tr("start_utc"),
                self.tr("end_utc"),
                self.tr("city"),
                self.tr("points"),
                self.tr("plan"),
                self.tr("name"),
                self.tr("format"),
                self.tr("message"),
                self.tr("activity"),
                self.tr("token"),
            ]
        )
        self.files_table.setHorizontalHeaderItem(0, QTableWidgetItem(self.tr("file")))
        header = self.files_table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setStretchLastSection(False)
        for column in range(self.files_table.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
        self.files_table.setColumnWidth(FILE_COLUMN, _text_column_width(self.files_table, "M" * 12))
        self.files_table.setColumnWidth(START_UTC_COLUMN, _text_column_width(self.files_table, "2026-07-02T08:51:58Z"))
        self.files_table.setColumnWidth(END_UTC_COLUMN, _text_column_width(self.files_table, "2026-07-02T08:51:58Z"))
        self.files_table.setColumnWidth(NAME_COLUMN, 280)
        self.files_table.setColumnWidth(MESSAGE_COLUMN, 260)
        self.files_table.setColumnWidth(ACTIVITY_COLUMN, 120)
        self.files_table.setColumnWidth(TOKEN_COLUMN, 170)
        self.files_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.files_table.setSortingEnabled(True)
        layout.addWidget(self.files_table, 1)

        actions = QHBoxLayout()
        self.inspect_button = QPushButton(self.tr("inspect"))
        self.run_button = QPushButton(self.tr("run"))
        self.clear_completed_button = QPushButton(self.tr("clear_completed"))
        actions.addStretch(1)
        actions.addWidget(self.inspect_button)
        actions.addWidget(self.run_button)
        actions.addWidget(self.clear_completed_button)
        layout.addLayout(actions)

        self.add_files_button.clicked.connect(self._add_files)
        self.add_folder_button.clicked.connect(self._add_folder)
        self.remove_files_button.clicked.connect(self._remove_selected_files)
        self.clear_completed_button.clicked.connect(self._clear_completed_files)
        self.clear_files_button.clicked.connect(self._clear_files)
        self.inspect_button.clicked.connect(self._inspect_files)
        self.run_button.clicked.connect(self._run_sync)
        return page

    def _build_purge_page(self) -> QWidget:
        page = QGroupBox(self.tr("nav_purge"))
        page.setObjectName("sectionGroup")
        layout = QHBoxLayout(page)

        self.purge_start = QDateEdit()
        self.purge_start.setCalendarPopup(True)
        self.purge_start.setDate(date(1970, 1, 1))
        self.purge_end = QDateEdit()
        self.purge_end.setCalendarPopup(True)
        self.purge_end.setDate(date.today())

        self.purge_button = QPushButton(self.tr("delete_matched"))
        layout.addWidget(QLabel(self.tr("start")))
        layout.addWidget(self.purge_start)
        layout.addWidget(QLabel(self.tr("end")))
        layout.addWidget(self.purge_end)
        layout.addStretch(1)
        layout.addWidget(self.purge_button)

        self.purge_button.clicked.connect(self._preview_then_purge)
        return page

    def _build_status_page(self) -> QWidget:
        page = QGroupBox(self.tr("status_log"))
        page.setObjectName("sectionGroup")
        layout = QVBoxLayout(page)
        self.status_output = QPlainTextEdit()
        self.status_output.setReadOnly(True)
        self.status_output.setMaximumHeight(120)
        layout.addWidget(self.status_output)
        return page

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            self.tr("add_track_files"),
            "",
            self.tr("track_files_filter"),
        )
        self._append_files(Path(path) for path in paths)

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, self.tr("add_folder_title"))
        if not folder:
            return
        root = Path(folder)
        files = []
        recursive = self._directory_has_children(root) and (
            QMessageBox.question(
                self,
                self.tr("recursive_add"),
                self.tr("add_folder_recursive"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            == QMessageBox.Yes
        )
        for pattern in ("*.CSV", "*.csv", "*.gpx", "*.GPX", "*.txt", "*.nmea", "*.log"):
            if recursive:
                files.extend(root.rglob(pattern))
            else:
                files.extend(root.glob(pattern))
        self._append_files(sorted(files))

    @staticmethod
    def _directory_has_children(path: Path) -> bool:
        try:
            for entry in path.iterdir():
                if entry.is_dir():
                    return True
        except OSError:
            return False
        return False

    def _append_files(self, paths) -> None:
        seen = set(self.files)
        added_paths = []
        for path in paths:
            if path not in seen and path.is_file():
                self.files.append(path)
                seen.add(path)
                added_paths.append(path)
        if added_paths:
            self._append_file_rows(added_paths)

    def _remove_selected_files(self) -> None:
        selected_paths = set()
        for index in self.files_table.selectedIndexes():
            item = self.files_table.item(index.row(), FILE_COLUMN)
            if item is not None and item.data(Qt.UserRole):
                selected_paths.add(Path(item.data(Qt.UserRole)))
        if selected_paths:
            self._remove_paths_from_table(selected_paths)

    def _clear_completed_files(self) -> None:
        completed_paths = self._completed_plan_files()
        if not completed_paths:
            message = self.tr("clear_completed_none")
            self.statusBar().showMessage(message)
            self._append_status_log(message)
            return
        removed_count = self._remove_paths_from_table(completed_paths)
        message = self.tr("clear_completed_done", count=removed_count)
        self.statusBar().showMessage(message)
        self._append_status_log(message)

    def _remove_paths_from_table(self, paths: set[Path]) -> int:
        if not paths:
            return 0
        self.files = [path for path in self.files if path not in paths]
        self.local_tracks = [
            track
            for track in self.local_tracks
            if track.track_file.source_path not in paths
        ]
        for path in paths:
            self._local_track_cache.pop(path, None)

        rows = []
        for row in range(self.files_table.rowCount()):
            item = self.files_table.item(row, FILE_COLUMN)
            if item is not None and item.data(Qt.UserRole) and Path(item.data(Qt.UserRole)) in paths:
                rows.append(row)

        sorting_enabled = self.files_table.isSortingEnabled()
        self.files_table.setSortingEnabled(False)
        for row in sorted(rows, reverse=True):
            self.files_table.removeRow(row)
        self.files_table.setSortingEnabled(sorting_enabled)
        if sorting_enabled:
            self.files_table.sortByColumn(START_UTC_COLUMN, Qt.AscendingOrder)
        return len(rows)

    def _clear_files(self) -> None:
        self.files = []
        self.local_tracks = []
        self._local_track_cache = {}
        self._render_file_paths()

    def _render_file_paths(self) -> None:
        sorting_enabled = self.files_table.isSortingEnabled()
        self.files_table.setSortingEnabled(False)
        self.files_table.setRowCount(len(self.files))
        for row, path in enumerate(self.files):
            self._set_row(self.files_table, row, [path.name, "", "", "", "", "", "", "", "", "", ""], source_path=path)
        self.files_table.setSortingEnabled(sorting_enabled)

    def _append_file_rows(self, paths: list[Path]) -> None:
        sorting_enabled = self.files_table.isSortingEnabled()
        self.files_table.setSortingEnabled(False)
        for path in paths:
            row = self.files_table.rowCount()
            self.files_table.insertRow(row)
            self._set_row(self.files_table, row, [path.name, "", "", "", "", "", "", "", "", "", ""], source_path=path)
        self.files_table.setSortingEnabled(sorting_enabled)

    def _inspect_files(self) -> None:
        if self._inspect_task_running:
            self._inspect_cancel_requested = True
            self.inspect_button.setEnabled(False)
            self.statusBar().showMessage(self.tr("stopping_check"))
            self._append_status_log(self.tr("stopping_check"))
            return
        if not self._require_files():
            return
        self._inspect_task_running = True
        self._inspect_cancel_requested = False
        options = self._sync_options(dry_run=True)
        files = self._table_files()
        precheck_local = self.tr("precheck_local", count=len(files))
        precheck_file = self.tr("precheck_file")
        precheck_ok = self.tr("precheck_ok")
        precheck_issue_summary = self.tr("precheck_issue_summary")
        self._run_task(
            self.tr("checking_files"),
            lambda emit: self._precheck_task(
                files,
                options,
                precheck_local,
                precheck_file,
                precheck_ok,
                precheck_issue_summary,
                emit,
            ),
            lambda bundle: self._on_precheck_done(bundle, options),
            on_error=lambda message: self._on_inspect_error(message),
            on_progress=self._on_progress_event,
            pass_progress=True,
        )

    def _run_sync(self) -> None:
        if self._sync_task_running:
            self._sync_cancel_requested = True
            self.run_button.setEnabled(False)
            self.statusBar().showMessage(self.tr("stopping_run"))
            self._append_status_log(self.tr("stopping_run"))
            return
        if not self._require_files():
            return
        self._sort_after_sync_done = False
        options = self._sync_options(dry_run=False)
        settings = self._garmin_settings()
        files = self._runnable_plan_files()
        if not files:
            message = self.tr("no_runnable_plans")
            self.statusBar().showMessage(message)
            self._append_status_log(message)
            return
        self._mark_files_queued(files)
        self._sync_task_running = True
        self._sync_cancel_requested = False
        queued_template = self.tr("sync_file_queued")
        connect_garmin = self.tr("connect_garmin")
        query_remote = self.tr("query_remote_sync")
        inspect_file = self.tr("inspect_file")
        self._run_task(
            self.tr("synchronizing"),
            lambda emit: self._sync_task(
                files,
                settings,
                options,
                queued_template,
                connect_garmin,
                query_remote,
                inspect_file,
                emit,
            ),
            self._on_sync_done,
            on_error=lambda message: self._on_sync_error(message),
            on_progress=self._on_progress_event,
            pass_progress=True,
        )

    def _login_or_logout(self) -> None:
        if self._authenticated:
            self._logout()
            return
        self._login()

    def _login_from_password_return(self) -> None:
        if self._authenticated or self._active_tasks or not self.password_input.text():
            return
        self.login_button.click()

    def _login(self) -> None:
        settings = self._garmin_settings()
        self._run_task(
            self.tr("logging_in"),
            lambda: self._login_task(settings),
            lambda user: self._on_login_ok(user, self.tr("login_complete"), settings.domain),
            on_error=lambda message: self._on_login_failed(message),
        )

    def _logout(self) -> None:
        shutil.rmtree(SESSION_DIR, ignore_errors=True)
        self._authenticated = False
        self._refresh_action_state()
        self._append_login_log(self.tr("logout_complete"))

    def _preview_then_purge(self) -> None:
        settings = self._garmin_settings()
        start_date, end_date = self._purge_settings()
        self._run_task(
            self.tr("previewing_purge"),
            lambda: self._purge_task(settings, start_date, end_date, dry_run=True),
            lambda summary: self._on_purge_preview_done(summary, settings, start_date, end_date),
        )

    def _on_purge_preview_done(
        self,
        summary: PurgeSummary,
        settings: GarminSettings,
        start_date: date,
        end_date: date,
    ) -> None:
        self._append_status_log(_format_purge(summary, self.tr, include_details=False))
        if not summary.decisions:
            QMessageBox.information(self, self.tr("confirm_purge"), self.tr("no_purge_matches"))
            return
        if not self._confirm_purge(summary):
            return
        self._run_task(
            self.tr("deleting_signed"),
            lambda: self._purge_task(settings, start_date, end_date, dry_run=False),
            self._on_purge_done,
        )

    def _confirm_purge(self, summary: PurgeSummary) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("confirm_purge"))
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(self.tr("confirm_purge_text")))

        table = QTableWidget(len(summary.decisions), 4)
        table.setHorizontalHeaderLabels(
            [
                self.tr("activity"),
                self.tr("name"),
                self.tr("start_utc"),
                self.tr("duration"),
            ]
        )
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setMinimumHeight(260)
        header = table.horizontalHeader()
        for row, decision in enumerate(summary.decisions):
            self._set_table_values(
                table,
                row,
                0,
                [
                    str(decision.activity_id),
                    decision.activity_name,
                    _format_activity_timestamp_ms(decision.begin_timestamp_ms),
                    _format_duration_adaptive(decision.duration_s),
                ],
            )
        table.resizeColumnsToContents()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        layout.addWidget(table)

        confirm_row = QHBoxLayout()
        confirm_row.addWidget(QLabel(self.tr("confirm_text")))
        confirm_input = QLineEdit()
        confirm_row.addWidget(confirm_input)
        layout.addLayout(confirm_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        ok_button.setEnabled(False)
        confirm_input.textChanged.connect(lambda text: ok_button.setEnabled(text == "DELETE"))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(760, 380)
        return dialog.exec() == QDialog.Accepted

    def _login_task(self, settings: GarminSettings) -> AuthenticatedUser:
        garmin = self._garmin(settings)
        fallback_username = settings.username or None
        garmin.ensure_session(fallback_username, settings.password or None, allow_prompt=False)
        return garmin.current_user(fallback_username=fallback_username)

    def _on_login_ok(self, user: AuthenticatedUser, message: str, domain: str | None = None) -> None:
        self._authenticated = True
        if domain:
            _save_saved_domain(domain, SESSION_DIR)
        login_name = user.email or user.username
        if login_name:
            self.username_input.setText(login_name)
        self._refresh_action_state()
        self._append_login_log(message)

    def _on_login_failed(self, message: str) -> None:
        self._authenticated = False
        self._refresh_action_state()
        self._show_error(message, context="login")

    def _status_task(self, settings: GarminSettings) -> AuthenticatedUser:
        garmin = self._garmin_authenticated(settings)
        garmin.ping()
        return garmin.current_user(fallback_username=settings.username or None)

    def _purge_task(
        self,
        settings: GarminSettings,
        start_date: date,
        end_date: date,
        dry_run: bool,
    ) -> PurgeSummary:
        return SyncService().purge(
            self._garmin_authenticated(settings),
            start_date=start_date,
            end_date=end_date,
            dry_run=dry_run,
            chunk_days=PURGE_CHUNK_DAYS,
        )

    def _precheck_task(
        self,
        files: list[Path],
        options: SyncOptions,
        precheck_local: str,
        precheck_file: str,
        precheck_ok: str,
        precheck_issue_summary: str,
        emit,
    ) -> InspectBundle:
        emit(("log", precheck_local))
        report: PrecheckReport
        tracks: list[LocalTrack] = []
        file_errors: list[FileCheckError] = []
        service = SyncService()
        count = len(files)
        canceled = False
        for index, path in enumerate(files, start=1):
            if self._inspect_cancel_requested:
                canceled = True
                break
            emit(("log", precheck_file.format(index=index, count=count, name=path.name)))
            try:
                local_track = self._cached_local_track(path, options)
                if local_track is not None:
                    emit(("log", self.tr("using_cached_track", name=path.name)))
                else:
                    local_track = service.inspect([path], options)[0]
                    self._cache_local_track(path, options, local_track)
                tracks.append(local_track)
                emit(("track", local_track))
            except Exception as exc:
                file_errors.append(FileCheckError(source_path=path, message=_format_file_exception(exc)))
                continue
        report = PrecheckService().check_tracks(tracks, file_errors=file_errors)
        if canceled:
            report = PrecheckReport(
                checked_count=report.checked_count,
                duplicate_groups=report.duplicate_groups,
                overlapping_points=report.overlapping_points,
                conflicting_points=report.conflicting_points,
                file_errors=report.file_errors,
                canceled=True,
            )
            emit(("log", self.tr("check_stopped")))
            return InspectBundle(report=report, tracks=tracks)
        if _precheck_has_issues(report):
            emit(("log", precheck_issue_summary))
        else:
            emit(("log", precheck_ok))
        return InspectBundle(report=report, tracks=tracks)

    def _on_precheck_done(self, bundle: InspectBundle, options: SyncOptions) -> None:
        report = bundle.report
        if report.canceled:
            self._on_inspect_canceled()
            return
        if _precheck_has_issues(report):
            self._append_status_log(_format_precheck(report, self.tr))
            issue_rows = self._mark_precheck_issue_rows(report)
            for path in issue_rows:
                self._local_track_cache.pop(path, None)
        else:
            issue_rows = set[Path]()
        failed_files = {item.source_path for item in report.file_errors}
        tracks = [
            track
            for track in bundle.tracks
            if track.track_file.source_path not in issue_rows
            and track.track_file.source_path not in failed_files
        ]
        for path in failed_files:
            self._local_track_cache.pop(path, None)
        self.local_tracks = tracks
        self._inspect_and_plan(tracks, options)

    def _mark_precheck_issue_rows(self, report: PrecheckReport) -> set[Path]:
        issue_files: dict[Path, set[str]] = {}
        issue_messages: dict[Path, list[str]] = {}

        def _add_issue(path: Path, plan: str, message: str) -> None:
            plans = issue_files.setdefault(path, set())
            plans.add(plan)
            issue_messages.setdefault(path, []).append(message)

        for item in report.file_errors:
            _add_issue(
                item.source_path,
                "file_error",
                self.tr("precheck_parse_error", message=item.message),
            )

        for group in report.duplicate_groups:
            source_paths = list(group.source_paths)
            other_count = max(0, len(source_paths) - 1)
            message = self.tr("precheck_duplicate_issue", count=str(other_count))
            for source_path in source_paths:
                _add_issue(source_path, "duplicate", message)

        for item in report.overlapping_points:
            first = item.first_source_path
            second = item.second_source_path
            message_first = self.tr(
                "precheck_overlap_issue",
                count=str(item.count),
                other=second.name,
            )
            message_second = self.tr(
                "precheck_overlap_issue",
                count=str(item.count),
                other=first.name,
            )
            _add_issue(first, "overlap", message_first)
            _add_issue(second, "overlap", message_second)

        for item in report.conflicting_points:
            first = item.first_source_path
            second = item.second_source_path
            message_first = self.tr(
                "precheck_conflict_issue",
                count=str(item.count),
                other=second.name,
            )
            message_second = self.tr(
                "precheck_conflict_issue",
                count=str(item.count),
                other=first.name,
            )
            _add_issue(first, "conflict", message_first)
            _add_issue(second, "conflict", message_second)

        for path, plans in issue_files.items():
            messages = issue_messages.get(path) or []
            plan = " / ".join(self._localize_precheck_issue_plan(plan) for plan in self._precheck_issue_plan_order(plans))
            message = "\n".join(messages)
            self._update_status_row(path, plan, message)
        return set(issue_files.keys())

    def _precheck_issue_plan_order(self, plans: set[str]) -> tuple[str, ...]:
        ordered = []
        for value in ("file_error", "duplicate", "overlap", "conflict"):
            if value in plans:
                ordered.append(value)
        return tuple(ordered)

    def _localize_precheck_issue_plan(self, plan: str) -> str:
        if plan == "file_error":
            return self.tr("plan_file_error")
        if plan == "duplicate":
            return self.tr("plan_duplicate")
        if plan == "overlap":
            return self.tr("plan_overlap")
        if plan == "conflict":
            return self.tr("plan_conflict")
        return plan

    def _inspect_and_plan(self, tracks: list[LocalTrack], options: SyncOptions) -> None:
        settings = self._garmin_settings()
        self._sort_after_sync_done = True
        waiting_plan = self.tr("waiting_plan")
        connect_garmin = self.tr("connect_garmin")
        query_remote = self.tr("query_remote")
        self._run_task(
            self.tr("planning_sync"),
            lambda emit: self._inspect_and_plan_task(
                tracks,
                settings,
                options,
                waiting_plan,
                connect_garmin,
                query_remote,
                emit,
            ),
            self._on_inspect_plan_done,
            on_error=lambda message: self._on_inspect_error(message),
            on_progress=self._on_progress_event,
            pass_progress=True,
        )

    def _inspect_and_plan_task(
        self,
        tracks: list[LocalTrack],
        settings: GarminSettings,
        options: SyncOptions,
        waiting_plan: str,
        connect_garmin: str,
        query_remote: str,
        emit,
    ) -> list[SyncDecision]:
        service = SyncService()
        if not tracks:
            return []
        for local_track in tracks:
            if self._inspect_cancel_requested:
                emit(("log", self.tr("check_stopped")))
                return []
            emit(("status", (local_track.track_file.source_path, waiting_plan, "")))
        if self._inspect_cancel_requested:
            emit(("log", self.tr("check_stopped")))
            return []
        emit(("log", connect_garmin))
        garmin = self._garmin_authenticated(settings)
        if self._inspect_cancel_requested:
            emit(("log", self.tr("check_stopped")))
            return []
        emit(("log", query_remote))
        return service.sync_tracks(
            tracks,
            garmin,
            options,
            on_decision=lambda decision: emit(("decision", decision)),
            on_progress=lambda event, track, details: emit(("sync-progress", (event, track, details))),
        )

    def _sync_task(
        self,
        files: list[Path],
        settings: GarminSettings,
        options: SyncOptions,
        queued_template: str,
        connect_garmin: str,
        query_remote: str,
        inspect_file: str,
        emit,
    ) -> list[SyncDecision]:
        service = SyncService()
        count = len(files)
        tracks: list[LocalTrack] = []
        failed_decisions: list[SyncDecision] = []
        for index, path in enumerate(files, start=1):
            if self._sync_cancel_requested:
                break
            emit(("log", inspect_file.format(index=index, count=count, name=path.name)))
            try:
                local_track = self._cached_local_track(path, options)
                if local_track is not None:
                    emit(("log", self.tr("using_cached_track", name=path.name)))
                else:
                    local_track = service.inspect([path], options)[0]
                    self._cache_local_track(path, options, local_track)
            except Exception as exc:
                decision = SyncDecision(
                    source_path=path,
                    status="failed",
                    token="",
                    planned_name="",
                    message=_format_file_exception(exc),
                )
                failed_decisions.append(decision)
                emit(("decision", decision))
                continue
            tracks.append(local_track)
            emit(("track", local_track))
            emit(("log", queued_template.format(index=index, count=count, name=path.name)))
        if not tracks:
            return failed_decisions
        emit(("log", connect_garmin))
        garmin = self._garmin_authenticated(settings)
        emit(("log", query_remote))
        decisions = service.sync_tracks(
            tracks,
            garmin,
            options,
            on_decision=lambda decision: emit(("decision", decision)),
            on_progress=lambda event, track, details: emit(("sync-progress", (event, track, details))),
            should_cancel=lambda: self._sync_cancel_requested,
        )
        return failed_decisions + decisions

    def _on_sync_done(self, decisions: list[SyncDecision]) -> None:
        was_canceled = self._sync_cancel_requested
        self._sync_task_running = False
        self._sync_cancel_requested = False
        if was_canceled:
            self._restore_queued_files()
        message = _format_decision_summary(decisions, self.tr, plan=False)
        self.statusBar().showMessage(message)
        self._append_status_log(message)
        if was_canceled:
            self.statusBar().showMessage(self.tr("run_stopped"))
            self._append_status_log(self.tr("run_stopped"))
        if getattr(self, "_sort_after_sync_done", False):
            self.files_table.sortItems(START_UTC_COLUMN, Qt.AscendingOrder)
            self._sort_after_sync_done = False

    def _on_sync_error(self, message: str) -> None:
        self._sync_task_running = False
        self._sync_cancel_requested = False
        self._queued_plan_snapshot = {}
        self._show_error(message)

    def _on_inspect_plan_done(self, decisions: list[SyncDecision]) -> None:
        if self._inspect_cancel_requested:
            self._on_inspect_canceled()
            return
        self._inspect_task_running = False
        self._inspect_cancel_requested = False
        message = _format_decision_summary(decisions, self.tr, plan=True)
        self.statusBar().showMessage(message)
        self._append_status_log(message)
        if getattr(self, "_sort_after_sync_done", False):
            self.files_table.sortItems(START_UTC_COLUMN, Qt.AscendingOrder)
            self._sort_after_sync_done = False
        self._refresh_action_state()

    def _on_inspect_canceled(self) -> None:
        self._inspect_task_running = False
        self._inspect_cancel_requested = False
        self.statusBar().showMessage(self.tr("check_stopped"))
        self._refresh_action_state()

    def _on_inspect_error(self, message: str) -> None:
        self._inspect_task_running = False
        self._inspect_cancel_requested = False
        self._refresh_action_state()
        self._show_error(message)

    def _on_progress_event(self, event) -> None:
        kind, payload = event
        if kind == "track":
            self._update_track_row(payload)
            self._remember_track(payload)
        elif kind == "decision":
            self._update_decision_row(payload)
        elif kind == "sync-progress":
            event, track, details = payload
            message = _format_sync_progress(event, track, details, self.tr)
            self._append_status_log(message)
            self._mark_file_uploading(track.track_file.source_path)
            self._update_message_row(track.track_file.source_path, message)
        elif kind == "status":
            path, plan, message = payload
            self._update_status_row(path, plan, message)
        elif kind == "log":
            self._append_status_log(payload)

    def _on_purge_done(self, summary: PurgeSummary) -> None:
        self._append_status_log(_format_purge(summary, self.tr))

    def _render_tracks(self, tracks: list[LocalTrack]) -> None:
        self.files = [track.track_file.source_path for track in tracks]
        self._render_file_paths()
        for track in tracks:
            self._update_track_row(track)
        self.statusBar().showMessage(self.tr("inspected_files", count=len(tracks)))

    def _update_track_row(self, track: LocalTrack) -> None:
        row = self._row_for_path(track.track_file.source_path)
        if row is None:
            return
        metadata = track.track_file.track.metadata
        if self._sync_task_running:
            sorting_enabled = self.files_table.isSortingEnabled()
            self.files_table.setSortingEnabled(False)
            self._set_table_values(
                self.files_table,
                row,
                FILE_COLUMN,
                [
                    track.track_file.source_path.name,
                    _format_utc_millis(metadata.start_time_utc),
                    _format_utc_millis(metadata.end_time_utc),
                    metadata.display_city or "",
                    str(metadata.point_count),
                ],
                source_path=track.track_file.source_path,
            )
            self._set_table_values(self.files_table, row, FORMAT_COLUMN, [track.track_file.source_format])
            self._set_table_values(self.files_table, row, TOKEN_COLUMN, [track.token])
            self.files_table.setSortingEnabled(sorting_enabled)
            return

        self._set_row(
            self.files_table,
            row,
            [
                track.track_file.source_path.name,
                _format_utc_millis(metadata.start_time_utc),
                _format_utc_millis(metadata.end_time_utc),
                metadata.display_city or "",
                str(metadata.point_count),
                "",
                "",
                track.track_file.source_format,
                "",
                "",
                track.token,
            ],
            source_path=track.track_file.source_path,
        )

    def _update_decision_row(self, decision: SyncDecision) -> None:
        row = self._row_for_path(decision.source_path)
        if row is None:
            return
        sorting_enabled = self.files_table.isSortingEnabled()
        self.files_table.setSortingEnabled(False)
        plan_text = _localized_plan_status(decision.status, self.tr)
        plan_status = decision.status
        if self._sync_task_running and decision.status in SUCCESSFUL_TASK_STATUSES:
            plan_text = self.tr("plan_completed")
            plan_status = "completed"
        self._set_plan_cell(row, plan_text, plan_status)
        self._set_table_values(self.files_table, row, NAME_COLUMN, [_decision_display_name(decision)])
        self._set_table_values(self.files_table, row, MESSAGE_COLUMN, [_localized_decision_message(decision.message, self.tr)])
        self._set_table_values(self.files_table, row, ACTIVITY_COLUMN, [str(decision.activity_id or "")])
        self.files_table.setSortingEnabled(sorting_enabled)

    def _update_status_row(self, path: Path, plan: str, message: str = "") -> None:
        row = self._row_for_path(path)
        if row is None:
            return
        sorting_enabled = self.files_table.isSortingEnabled()
        self.files_table.setSortingEnabled(False)
        self._set_plan_cell(row, plan)
        self._set_table_values(self.files_table, row, MESSAGE_COLUMN, [message])
        self.files_table.setSortingEnabled(sorting_enabled)

    def _update_message_row(self, path: Path, message: str) -> None:
        row = self._row_for_path(path)
        if row is None:
            return
        sorting_enabled = self.files_table.isSortingEnabled()
        self.files_table.setSortingEnabled(False)
        self._set_table_values(self.files_table, row, MESSAGE_COLUMN, [message])
        self.files_table.setSortingEnabled(sorting_enabled)

    def _mark_files_queued(self, files: list[Path]) -> None:
        self._queued_plan_snapshot = {}
        sorting_enabled = self.files_table.isSortingEnabled()
        self.files_table.setSortingEnabled(False)
        for path in files:
            row = self._row_for_path(path)
            if row is not None:
                plan_item = self.files_table.item(row, PLAN_COLUMN)
                if plan_item is not None:
                    self._queued_plan_snapshot[path] = (plan_item.text(), plan_item.data(PLAN_STATUS_ROLE))
                self._set_plan_cell(row, self.tr("plan_queued"), "queued")
                self._set_table_values(self.files_table, row, MESSAGE_COLUMN, [""])
        self.files_table.setSortingEnabled(sorting_enabled)

    def _restore_queued_files(self) -> None:
        sorting_enabled = self.files_table.isSortingEnabled()
        self.files_table.setSortingEnabled(False)
        for path, (text, status) in self._queued_plan_snapshot.items():
            row = self._row_for_path(path)
            if row is None:
                continue
            plan_item = self.files_table.item(row, PLAN_COLUMN)
            if plan_item is not None and plan_item.data(PLAN_STATUS_ROLE) == "queued":
                self._set_plan_cell(row, text, status)
        self.files_table.setSortingEnabled(sorting_enabled)
        self._queued_plan_snapshot = {}

    def _mark_file_uploading(self, path: Path) -> None:
        row = self._row_for_path(path)
        if row is None:
            return
        plan_item = self.files_table.item(row, PLAN_COLUMN)
        if plan_item is not None and plan_item.data(PLAN_STATUS_ROLE) == "completed":
            return
        sorting_enabled = self.files_table.isSortingEnabled()
        self.files_table.setSortingEnabled(False)
        self._set_plan_cell(row, self.tr("plan_uploading"), "uploading")
        self.files_table.setSortingEnabled(sorting_enabled)

    def _set_plan_cell(self, row: int, text: str, status: str | None = None) -> None:
        self._set_table_values(self.files_table, row, PLAN_COLUMN, [text])
        item = self.files_table.item(row, PLAN_COLUMN)
        if item is not None:
            item.setData(PLAN_STATUS_ROLE, status)

    def _runnable_plan_files(self) -> list[Path]:
        runnable_files = []
        for row in range(self.files_table.rowCount()):
            plan_item = self.files_table.item(row, PLAN_COLUMN)
            file_item = self.files_table.item(row, FILE_COLUMN)
            if (
                plan_item is not None
                and file_item is not None
                and plan_item.data(PLAN_STATUS_ROLE) in RUNNABLE_PLAN_STATUSES
                and file_item.data(Qt.UserRole)
            ):
                runnable_files.append(Path(file_item.data(Qt.UserRole)))
        return runnable_files

    def _table_files(self) -> list[Path]:
        files = []
        for row in range(self.files_table.rowCount()):
            item = self.files_table.item(row, FILE_COLUMN)
            if item is not None and item.data(Qt.UserRole):
                files.append(Path(item.data(Qt.UserRole)))
        return files

    def _completed_plan_files(self) -> set[Path]:
        completed_paths = set()
        for row in range(self.files_table.rowCount()):
            plan_item = self.files_table.item(row, PLAN_COLUMN)
            file_item = self.files_table.item(row, FILE_COLUMN)
            if (
                plan_item is not None
                and file_item is not None
                and plan_item.data(PLAN_STATUS_ROLE) in COMPLETED_PLAN_STATUSES
                and file_item.data(Qt.UserRole)
            ):
                completed_paths.add(Path(file_item.data(Qt.UserRole)))
        return completed_paths

    def _remember_track(self, track: LocalTrack) -> None:
        self.local_tracks = [
            existing
            for existing in self.local_tracks
            if existing.track_file.source_path != track.track_file.source_path
        ]
        self.local_tracks.append(track)

    def _track_cache_key(self, path: Path, options: SyncOptions) -> tuple[int, int, FormatOptions]:
        stat = path.stat()
        return (stat.st_size, stat.st_mtime_ns, options.format_options)

    def _cache_local_track(self, path: Path, options: SyncOptions, track: LocalTrack) -> None:
        self._local_track_cache[path] = (*self._track_cache_key(path, options), track)

    def _cached_local_track(self, path: Path, options: SyncOptions) -> LocalTrack | None:
        cached = self._local_track_cache.get(path)
        if cached is None:
            return None
        size, mtime_ns, cached_format, track = cached
        current_size, current_mtime_ns, current_format = self._track_cache_key(path, options)
        if (size, mtime_ns, cached_format) == (current_size, current_mtime_ns, current_format):
            return track
        self._local_track_cache.pop(path, None)
        return None

    def _row_for_path(self, path: Path) -> int | None:
        normalized = str(path)
        for row in range(self.files_table.rowCount()):
            item = self.files_table.item(row, FILE_COLUMN)
            if item is not None and item.data(Qt.UserRole) == normalized:
                return row
        return None

    def _run_task(self, label: str, task, on_result, on_error=None, on_progress=None, pass_progress: bool = False) -> None:
        self._active_tasks += 1
        self.status.setRange(0, 0)
        self.statusBar().showMessage(label)
        self._append_status_log(label)
        self._refresh_action_state()
        worker = TaskWorker(task, pass_progress=pass_progress)
        self._workers.append(worker)
        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error or self._show_error)
        if on_progress is not None:
            worker.signals.progress.connect(on_progress)
        worker.signals.finished.connect(lambda worker=worker: self._task_finished(worker))
        self.thread_pool.start(worker)

    def _task_finished(self, worker: TaskWorker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)
        worker.signals.deleteLater()
        self._active_tasks = max(0, self._active_tasks - 1)
        if self._active_tasks == 0:
            self.status.setRange(0, 1)
            self.status.setValue(1)
            self._refresh_action_state()

    def _show_error(self, message: str, context: str = "default") -> None:
        self.statusBar().showMessage(self.tr("failed"))
        self._append_status_log(message)
        summary = _friendly_error_message(message, self.tr, context=context)
        ErrorDialog(self, self.tr("error"), summary, message, self.tr).exec()

    def _refresh_action_state(self) -> None:
        task_running = self._active_tasks > 0
        login_inputs_enabled = not task_running and not self._authenticated
        self.global_domain_radio.setEnabled(login_inputs_enabled)
        self.china_domain_radio.setEnabled(login_inputs_enabled)
        self.username_input.setEnabled(login_inputs_enabled)
        self.password_input.setEnabled(login_inputs_enabled)
        self.login_button.setEnabled(not task_running)
        self.login_button.setText(self.tr("logout") if self._authenticated else self.tr("login"))

        authenticated_idle = self._authenticated and not task_running
        self.purge_start.setEnabled(authenticated_idle)
        self.purge_end.setEnabled(authenticated_idle)
        self.purge_button.setEnabled(authenticated_idle)

        self.files_table.setEnabled(self._authenticated)
        file_controls_enabled = authenticated_idle
        for widget in (
            self.add_files_button,
            self.add_folder_button,
            self.remove_files_button,
            self.clear_completed_button,
            self.clear_files_button,
        ):
            widget.setEnabled(file_controls_enabled)
        self.inspect_button.setEnabled(authenticated_idle or self._inspect_task_running)
        self.inspect_button.setText(self.tr("stop_inspect") if self._inspect_task_running else self.tr("inspect"))
        self.run_button.setEnabled(authenticated_idle or (self._sync_task_running and not self._sync_cancel_requested))
        self.run_button.setText(self.tr("stop_run") if self._sync_task_running else self.tr("run"))

    def _resize_to_initial_size(self) -> None:
        central = self.centralWidget()
        if central is not None and central.layout() is not None:
            central.layout().activate()
        minimum = self.minimumSizeHint().expandedTo(self.minimumSize())
        if minimum.isValid():
            row_height = max(self.files_table.verticalHeader().defaultSectionSize(), self.files_table.fontMetrics().height())
            self.resize(minimum.width(), minimum.height() + row_height * 8)

    def _require_files(self) -> bool:
        if self.files:
            return True
        QMessageBox.information(self, self.tr("no_files_title"), self.tr("no_files"))
        return False

    def _sync_options(self, dry_run: bool) -> SyncOptions:
        return SyncOptions(
            format_options=FormatOptions(),
            dry_run=dry_run,
            sort_for_upload=False,
        )

    def _garmin_settings(self) -> GarminSettings:
        return GarminSettings(
            domain="garmin.cn" if self.china_domain_radio.isChecked() else "garmin.com",
            session_dir=SESSION_DIR,
            username=self.username_input.text().strip(),
            password=self.password_input.text(),
        )

    def _purge_settings(self) -> tuple[date, date]:
        return self.purge_start.date().toPython(), self.purge_end.date().toPython()

    def _garmin(self, settings: GarminSettings) -> GarminClient:
        return GarminClient(domain=settings.domain, session_dir=settings.session_dir)

    def _garmin_authenticated(self, settings: GarminSettings) -> GarminClient:
        garmin = self._garmin(settings)
        garmin.ensure_session(settings.username or None, settings.password or None, allow_prompt=False)
        return garmin

    def _append_login_log(self, message: str) -> None:
        self._append_status_log(message)

    def _append_status_log(self, message: str) -> None:
        for line in _timestamped_log_lines(message):
            self.status_output.appendPlainText(line)

    def _set_status_log(self, message: str) -> None:
        self.status_output.setPlainText("\n".join(_timestamped_log_lines(message)))

    def _set_row(self, table: QTableWidget, row: int, values: list[str], source_path: Path | None = None) -> None:
        self._set_table_values(table, row, 0, values, source_path=source_path)

    def _set_table_values(
        self,
        table: QTableWidget,
        row: int,
        start_column: int,
        values: list[str],
        source_path: Path | None = None,
    ) -> None:
        sorting_enabled = table.isSortingEnabled()
        table.setSortingEnabled(False)
        for offset, value in enumerate(values):
            column = start_column + offset
            item = SortableTableItem(value)
            item.setFlags(item.flags() ^ Qt.ItemIsEditable)
            numeric_value = _numeric_sort_value(column, value)
            if numeric_value is not None:
                item.setData(Qt.UserRole + 2, numeric_value)
            if source_path is not None and column == FILE_COLUMN:
                item.setData(Qt.UserRole, str(source_path))
                item.setToolTip(str(source_path.resolve()))
            table.setItem(row, column, item)
        table.setSortingEnabled(sorting_enabled)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._active_tasks:
            QMessageBox.information(self, self.tr("task_running"), self.tr("task_running_text"))
            event.ignore()
            return
        event.accept()

    def _restore_saved_domain(self) -> None:
        domain = _load_saved_domain(SESSION_DIR)
        if domain == "garmin.com":
            self.global_domain_radio.setChecked(True)
        elif domain == "garmin.cn":
            self.china_domain_radio.setChecked(True)

    def _auto_check_session(self) -> None:
        settings = self._garmin_settings()
        self._run_task(
            self.tr("checking_session"),
            lambda: self._status_task(settings),
            lambda user: self._on_auto_session_ok(user, settings.domain),
            on_error=lambda message: self._on_auto_session_failed(message),
        )

    def _on_auto_session_ok(self, user: AuthenticatedUser, domain: str) -> None:
        self._on_login_ok(user, self.tr("session_usable"), domain)

    def _on_auto_session_failed(self, message: str) -> None:
        self._authenticated = False
        self._refresh_action_state()
        friendly_message = self.tr("no_valid_session")
        self.statusBar().showMessage(friendly_message)
        self._append_login_log(friendly_message)


def _precheck_has_issues(report: PrecheckReport) -> bool:
    return bool(report.duplicate_groups or report.overlapping_points or report.conflicting_points or report.file_errors)


def _format_utc_millis(value) -> str:
    utc_value = value.astimezone(timezone.utc)
    return utc_value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc_value.microsecond // 1000:03d}Z"


def _format_activity_timestamp_ms(value: int | None) -> str:
    if value is None:
        return ""
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _numeric_sort_value(column: int, value: str) -> int | None:
    if column not in {POINTS_COLUMN, ACTIVITY_COLUMN} or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _format_duration_adaptive(duration_s: float | None) -> str:
    if duration_s is None:
        return ""
    seconds = max(0, int(round(duration_s)))
    if seconds < 60:
        return f"{seconds}s"
    minutes, remaining_seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {remaining_seconds}s" if remaining_seconds else f"{minutes}m"
    hours, remaining_minutes = divmod(minutes, 60)
    if remaining_minutes:
        return f"{hours}h {remaining_minutes}m"
    return f"{hours}h"


def _localized_plan_status(status: str, tr) -> str:
    if status == "upload":
        return tr("plan_upload")
    if status in {"skip-token"}:
        return tr("plan_skip")
    if status in {"skip-legacy-match", "backfilled-token"}:
        return tr("plan_backfill_token")
    if status == "upload-conflict":
        return tr("plan_upload_conflict")
    if status == "ambiguous":
        return tr("plan_ambiguous")
    if status == "failed":
        return tr("plan_failed")
    return status


def _decision_display_name(decision: SyncDecision) -> str:
    if len(decision.candidates) == 1:
        return decision.candidates[0].activity_name
    return decision.planned_name


def _localized_decision_message(message: str, tr) -> str:
    fixed = {
        "duplicate local track in same batch": "decision_duplicate_local",
        "token added to existing activity": "decision_token_added",
        "uploaded and tagged": "decision_uploaded_tagged",
        "token already exists": "decision_token_exists",
        "token backfilled": "decision_token_backfilled",
        "would backfill token": "decision_would_backfill",
        "multiple legacy matches": "decision_multiple_legacy",
        "no matching remote activity": "decision_no_matching_remote",
        "signed GCU activity": "decision_signed_gcu",
        "signed GCU activity deleted": "decision_signed_deleted",
        "remote token match": "decision_remote_token_match",
        "legacy activity already has token": "decision_legacy_has_token",
        "legacy activity match": "decision_legacy_match",
        "no duplicate found": "decision_no_duplicate",
        "Garmin reported duplicate; token added to matched activity": "decision_duplicate_added_token",
        "Garmin reported duplicate; multiple remote matches": "decision_duplicate_multiple",
        "Garmin reported duplicate; no unique remote match": "decision_duplicate_no_unique",
    }
    key = fixed.get(message)
    if key:
        return tr(key)

    match = re.fullmatch(r"uploaded; activity unavailable after (?P<wait_s>\d+)s, not tagged", message)
    if match:
        return tr("decision_upload_unavailable", wait_s=match.group("wait_s"))

    return message


def _format_sync_progress(event: str, track: LocalTrack, details: dict, tr) -> str:
    key_by_event = {
        "planning": "sync_progress_planning",
        "write-fit": "sync_progress_write_fit",
        "upload": "sync_progress_upload",
        "wait-uploaded": "sync_progress_wait_uploaded",
        "update-name": "sync_progress_update_name",
        "backfill-token": "sync_progress_backfill_token",
        "resolve-conflict": "sync_progress_resolve_conflict",
    }
    key = key_by_event.get(event)
    name = track.track_file.source_path.name
    if key is None:
        return f"{event}: {name}"
    return tr(key, name=name, wait_s=details.get("wait_s", ""))


def _format_decision_summary(decisions: list[SyncDecision], tr, plan: bool) -> str:
    counts = _decision_counts(decisions)
    key = "completed_plan_summary" if plan else "completed_task_summary"
    return tr(
        key,
        count=len(decisions),
        upload=counts["upload"],
        skip=counts["skip"],
        backfill=counts["backfill"],
        failed=counts["failed"],
        other=counts["other"],
    )


def _decision_counts(decisions: list[SyncDecision]) -> dict[str, int]:
    counts = {"upload": 0, "skip": 0, "backfill": 0, "failed": 0, "other": 0}
    for decision in decisions:
        if decision.status == "upload":
            counts["upload"] += 1
        elif decision.status == "skip-token":
            counts["skip"] += 1
        elif decision.status in {"skip-legacy-match", "backfilled-token"}:
            counts["backfill"] += 1
        elif decision.status == "failed":
            counts["failed"] += 1
        else:
            counts["other"] += 1
    return counts


def _timestamped_log_lines(message: str) -> list[str]:
    if not message:
        return []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = message.splitlines() or [message]
    return [f"[{timestamp}] {line}" if line else f"[{timestamp}]" for line in lines]


def _friendly_error_message(message: str, tr, context: str = "default") -> str:
    lowered = message.lower()
    last_line = _last_traceback_line(message)
    detail = f"\n\n{last_line}" if last_line else ""

    if context == "login":
        return tr("error_login_summary") + detail
    if any(text in lowered for text in ("unauthorized", "forbidden", "401", "403", "invalid token", "token expired")):
        return tr("error_auth_summary") + detail
    if any(text in lowered for text in ("connectionerror", "timeout", "timed out", "name resolution", "proxyerror", "sslerror")):
        return tr("error_network_summary") + detail
    if any(text in lowered for text in ("garthhttperror", "http error", "status_code", "500", "502", "503", "504")):
        return tr("error_server_summary") + detail
    return tr("error_default_summary") + detail


def _last_traceback_line(message: str) -> str:
    for line in reversed(message.splitlines()):
        text = line.strip()
        if text:
            return text
    return ""


def _format_file_exception(exc: Exception) -> str:
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def _text_column_width(widget: QWidget, sample: str) -> int:
    return widget.fontMetrics().horizontalAdvance(sample) + 32


def _format_precheck(report: PrecheckReport, tr) -> str:
    lines = [
        f"{tr('checked_files')}: {report.checked_count}",
        f"{tr('duplicate_groups')}: {len(report.duplicate_groups)}",
        f"{tr('overlapping_pairs')}: {len(report.overlapping_points)}",
        f"{tr('conflicting_pairs')}: {len(report.conflicting_points)}",
        f"{tr('file_errors')}: {len(report.file_errors)}",
        "",
    ]
    if report.file_errors:
        lines.append(tr("file_errors"))
        for item in report.file_errors:
            lines.append(f"  {item.source_path}: {item.message}")
        lines.append("")
    if report.duplicate_groups:
        lines.append(tr("duplicate_tracks"))
        for group in report.duplicate_groups:
            lines.append(f"  {group.token}")
            for path in group.source_paths:
                lines.append(f"    {path}")
        lines.append("")
    if report.overlapping_points:
        lines.append(tr("overlapping_points"))
        for item in report.overlapping_points:
            lines.append(f"  {item.first_source_path} <-> {item.second_source_path}: {item.count}")
        lines.append("")
    if report.conflicting_points:
        lines.append(tr("conflicting_points"))
        for item in report.conflicting_points:
            lines.append(f"  {item.first_source_path} <-> {item.second_source_path}: {item.count}")
            for example in item.examples:
                lines.append(
                    "    "
                    f"{example.timestamp_utc.isoformat()} "
                    f"{tr('first')}=({example.first_latitude:.7f},{example.first_longitude:.7f}) "
                    f"{tr('second')}=({example.second_latitude:.7f},{example.second_longitude:.7f})"
                )
    return "\n".join(lines).rstrip()


def _format_purge(summary: PurgeSummary, tr, include_details: bool = True) -> str:
    action = tr("would_delete") if summary.dry_run else tr("deleted")
    lines = [
        f"{tr('date_range')}: {summary.start_date.isoformat()} to {summary.end_date.isoformat()}",
        f"{tr('scanned')}: {summary.scanned_count}",
        f"{tr('matched')}: {summary.matched_count}",
        f"{tr('skipped_unsigned')}: {summary.skipped_unsigned_count}",
        f"{action}: {len(summary.decisions)}",
    ]
    if not include_details:
        return "\n".join(lines).rstrip()
    lines.append("")
    for decision in summary.decisions:
        lines.append(
            f"{decision.status} activity={decision.activity_id} "
            f"{tr('manufacturer')}={decision.manufacturer} {tr('device_id')}={decision.device_id} "
            f"{decision.activity_name}"
        )
    return "\n".join(lines).rstrip()


def main() -> int:
    _set_windows_app_user_model_id()
    app = QApplication(sys.argv)
    _apply_application_style(app)
    icon = QIcon(str(_resource_path(APP_ICON_PATH)))
    app.setWindowIcon(icon)
    window = MainWindow()
    window.setWindowIcon(icon)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
