from __future__ import annotations

import sys
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from PySide6.QtCore import QLocale, QThreadPool, QTimer, Qt
from PySide6.QtGui import QCloseEvent
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

from gcu.app.models import AuthenticatedUser, LocalTrack, PrecheckReport, PurgeSummary, SyncDecision
from gcu.app.precheck_service import PrecheckService
from gcu.app.sync_service import SyncOptions, SyncService
from gcu.formats.base import FormatOptions
from gcu.garmin import GarminClient
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


SESSION_DIR = Path(".garth_session")
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
        "clear": "Clear",
        "inspect": "Inspect",
        "run": "Run",
        "file": "File",
        "format": "Format",
        "points": "Points",
        "start_utc": "Start UTC",
        "end_utc": "End UTC",
        "city": "City",
        "token": "Token",
        "plan": "Plan",
        "activity": "Activity",
        "name": "Name",
        "message": "Message",
        "warnings": "Warnings",
        "start": "Start",
        "end": "End",
        "duration": "Duration",
        "delete_matched": "Clean Uploaded Tracks",
        "add_track_files": "Add track files",
        "track_files_filter": "Track files (*.CSV *.csv *.txt *.nmea *.log);;All files (*)",
        "add_folder_title": "Add folder",
        "checking_files": "Checking files",
        "precheck_local": "Running local pre-check for {count} files",
        "precheck_ok": "Local pre-check passed",
        "precheck_issue_summary": "Local pre-check found duplicate, overlap, or conflict issues",
        "inspect_file": "Inspecting file {index}/{count}: {name}",
        "inspect_file_done": "Inspected file {index}/{count}: {name}",
        "connect_garmin": "Connecting to Garmin Connect",
        "query_remote": "Querying remote activities and planning dry-run decisions",
        "sync_file_queued": "Queued for upload decision {index}/{count}: {name}",
        "synchronizing": "Synchronizing tracks",
        "logging_in": "Logging in",
        "checking_session": "Checking session",
        "previewing_purge": "Previewing purge",
        "deleting_signed": "Deleting signed activities",
        "confirm_purge": "Confirm purge",
        "confirm_purge_text": "The following uploaded tracks will be deleted. Type DELETE to continue.",
        "confirm_text": "Confirm",
        "no_purge_matches": "No uploaded tracks matched the cleanup criteria.",
        "login_complete": "Login complete.",
        "session_usable": "Session is usable.",
        "precheck_issues": "Pre-check found issues",
        "continue_dry_run": "Continue to dry-run?",
        "inspect_stopped": "Inspect stopped after pre-check",
        "planning_sync": "Planning sync",
        "waiting_plan": "Waiting for sync plan",
        "completed_decisions": "Completed {count} decisions",
        "inspected_files": "Inspected {count} files",
        "no_files_title": "No files",
        "no_files": "Add one or more track files first.",
        "failed": "Failed",
        "error": "Error",
        "task_running": "Task running",
        "task_running_text": "Wait for the current task to finish before closing.",
        "checked_files": "Checked files",
        "duplicate_groups": "Duplicate groups",
        "overlapping_pairs": "Overlapping point pairs",
        "conflicting_pairs": "Conflicting point pairs",
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
        "clear": "清空",
        "inspect": "检查",
        "run": "运行",
        "file": "文件",
        "format": "格式",
        "points": "点数",
        "start_utc": "开始 UTC",
        "end_utc": "结束 UTC",
        "city": "城市",
        "token": "Token",
        "plan": "计划",
        "activity": "活动",
        "name": "名称",
        "message": "消息",
        "warnings": "警告",
        "start": "开始",
        "end": "结束",
        "duration": "时长",
        "delete_matched": "清理已上传轨迹",
        "add_track_files": "添加轨迹文件",
        "track_files_filter": "轨迹文件 (*.CSV *.csv *.txt *.nmea *.log);;所有文件 (*)",
        "add_folder_title": "添加文件夹",
        "checking_files": "正在检查文件",
        "precheck_local": "正在对 {count} 个文件做本地合法性检查",
        "precheck_ok": "本地合法性检查通过",
        "precheck_issue_summary": "本地合法性检查发现重复、重叠或冲突问题",
        "inspect_file": "正在处理文件 {index}/{count}: {name}",
        "inspect_file_done": "文件处理完成 {index}/{count}: {name}",
        "connect_garmin": "正在连接 Garmin Connect",
        "query_remote": "正在查询远端活动并生成 dry-run 决策",
        "sync_file_queued": "等待同步决策 {index}/{count}: {name}",
        "synchronizing": "正在同步轨迹",
        "logging_in": "正在登录",
        "checking_session": "正在检查登录",
        "previewing_purge": "正在预览清理",
        "deleting_signed": "正在删除签名活动",
        "confirm_purge": "确认清理",
        "confirm_purge_text": "以下已上传轨迹将被删除。输入 DELETE 以继续。",
        "confirm_text": "确认",
        "no_purge_matches": "没有发现符合清理条件的已上传轨迹。",
        "login_complete": "登录完成。",
        "session_usable": "登录凭证可用。",
        "precheck_issues": "预检查发现问题",
        "continue_dry_run": "是否继续执行 dry-run？",
        "inspect_stopped": "预检查后已停止检查",
        "planning_sync": "正在生成同步计划",
        "waiting_plan": "等待同步计划",
        "completed_decisions": "完成 {count} 个决策",
        "inspected_files": "已检查 {count} 个文件",
        "no_files_title": "没有文件",
        "no_files": "请先添加一个或多个轨迹文件。",
        "failed": "失败",
        "error": "错误",
        "task_running": "任务运行中",
        "task_running_text": "请等待当前任务结束后再关闭。",
        "checked_files": "已检查文件数",
        "duplicate_groups": "重复轨迹组",
        "overlapping_pairs": "重叠点文件对",
        "conflicting_pairs": "冲突点文件对",
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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.lang = _language()
        self.texts = TRANSLATIONS[self.lang]
        self.setWindowTitle(self.tr("title"))
        self.thread_pool = QThreadPool.globalInstance()
        self.files: list[Path] = []
        self.local_tracks: list[LocalTrack] = []
        self._active_tasks = 0
        self._workers: list[TaskWorker] = []
        self._authenticated = False
        self._sort_after_sync_done = False

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        self.login_page = self._build_login_page()
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
        return page

    def _build_files_page(self) -> QWidget:
        page = QGroupBox(self.tr("nav_files"))
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
        self.files_table.setColumnWidth(FILE_COLUMN, _text_column_width(self.files_table, "M" * 25))
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
        actions.addStretch(1)
        actions.addWidget(self.inspect_button)
        actions.addWidget(self.run_button)
        layout.addLayout(actions)

        self.add_files_button.clicked.connect(self._add_files)
        self.add_folder_button.clicked.connect(self._add_folder)
        self.remove_files_button.clicked.connect(self._remove_selected_files)
        self.clear_files_button.clicked.connect(self._clear_files)
        self.inspect_button.clicked.connect(self._inspect_files)
        self.run_button.clicked.connect(self._run_sync)
        return page

    def _build_purge_page(self) -> QWidget:
        page = QGroupBox(self.tr("nav_purge"))
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
        for pattern in ("*.CSV", "*.csv", "*.txt", "*.nmea", "*.log"):
            files.extend(root.glob(pattern))
        self._append_files(sorted(files))

    def _append_files(self, paths) -> None:
        seen = set(self.files)
        for path in paths:
            if path not in seen and path.is_file():
                self.files.append(path)
                seen.add(path)
        self.local_tracks = []
        self._render_file_paths()

    def _remove_selected_files(self) -> None:
        selected_paths = set()
        for index in self.files_table.selectedIndexes():
            item = self.files_table.item(index.row(), FILE_COLUMN)
            if item is not None and item.data(Qt.UserRole):
                selected_paths.add(Path(item.data(Qt.UserRole)))
        if selected_paths:
            self.files = [path for path in self.files if path not in selected_paths]
        self.local_tracks = []
        self._render_file_paths()

    def _clear_files(self) -> None:
        self.files = []
        self.local_tracks = []
        self._render_file_paths()

    def _render_file_paths(self) -> None:
        sorting_enabled = self.files_table.isSortingEnabled()
        self.files_table.setSortingEnabled(False)
        self.files_table.setRowCount(len(self.files))
        for row, path in enumerate(self.files):
            self._set_row(self.files_table, row, [path.name, "", "", "", "", "", "", "", "", "", ""], source_path=path)
        self.files_table.setSortingEnabled(sorting_enabled)

    def _inspect_files(self) -> None:
        if not self._require_files():
            return
        options = self._sync_options(dry_run=True)
        files = list(self.files)
        precheck_local = self.tr("precheck_local", count=len(files))
        precheck_ok = self.tr("precheck_ok")
        precheck_issue_summary = self.tr("precheck_issue_summary")
        self._run_task(
            self.tr("checking_files"),
            lambda emit: self._precheck_task(
                files,
                options,
                precheck_local,
                precheck_ok,
                precheck_issue_summary,
                emit,
            ),
            lambda report: self._on_precheck_done(report, files, options),
            on_progress=self._on_progress_event,
            pass_progress=True,
        )

    def _run_sync(self) -> None:
        if not self._require_files():
            return
        self._sort_after_sync_done = False
        options = self._sync_options(dry_run=False)
        settings = self._garmin_settings()
        files = list(self.files)
        queued_template = self.tr("sync_file_queued")
        connect_garmin = self.tr("connect_garmin")
        query_remote = self.tr("query_remote")
        inspect_file = self.tr("inspect_file")
        inspect_file_done = self.tr("inspect_file_done")
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
                inspect_file_done,
                emit,
            ),
            self._on_sync_done,
            on_progress=self._on_progress_event,
            pass_progress=True,
        )

    def _login_or_logout(self) -> None:
        if self._authenticated:
            self._logout()
            return
        self._login()

    def _login(self) -> None:
        settings = self._garmin_settings()
        self._run_task(
            self.tr("logging_in"),
            lambda: self._login_task(settings),
            lambda user: self._on_login_ok(user, self.tr("login_complete")),
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
        self._append_status_log(_format_purge(summary, self.tr))
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

    def _on_login_ok(self, user: AuthenticatedUser, message: str) -> None:
        self._authenticated = True
        if user.username:
            self.username_input.setText(user.username)
        self._refresh_action_state()
        self._append_login_log(message)

    def _on_login_failed(self, message: str) -> None:
        self._authenticated = False
        self._refresh_action_state()
        self._show_error(message)

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
        precheck_ok: str,
        precheck_issue_summary: str,
        emit,
    ) -> PrecheckReport:
        emit(("log", precheck_local))
        report = PrecheckService().check(files, options)
        if _precheck_has_issues(report):
            emit(("log", precheck_issue_summary))
        else:
            emit(("log", precheck_ok))
        return report

    def _on_precheck_done(self, report: PrecheckReport, files: list[Path], options: SyncOptions) -> None:
        if _precheck_has_issues(report):
            self._append_status_log(_format_precheck(report, self.tr))
            answer = QMessageBox.warning(
                self,
                self.tr("precheck_issues"),
                _format_precheck(report, self.tr) + "\n\n" + self.tr("continue_dry_run"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self.statusBar().showMessage(self.tr("inspect_stopped"))
                self._append_status_log(self.tr("inspect_stopped"))
                return
        self._inspect_and_plan(files, options)

    def _inspect_and_plan(self, files: list[Path], options: SyncOptions) -> None:
        settings = self._garmin_settings()
        self._sort_after_sync_done = True
        waiting_plan = self.tr("waiting_plan")
        inspect_file = self.tr("inspect_file")
        inspect_file_done = self.tr("inspect_file_done")
        connect_garmin = self.tr("connect_garmin")
        query_remote = self.tr("query_remote")
        self._run_task(
            self.tr("planning_sync"),
            lambda emit: self._inspect_and_plan_task(
                files,
                settings,
                options,
                waiting_plan,
                inspect_file,
                inspect_file_done,
                connect_garmin,
                query_remote,
                emit,
            ),
            self._on_sync_done,
            on_progress=self._on_progress_event,
            pass_progress=True,
        )

    def _inspect_and_plan_task(
        self,
        files: list[Path],
        settings: GarminSettings,
        options: SyncOptions,
        waiting_plan: str,
        inspect_file: str,
        inspect_file_done: str,
        connect_garmin: str,
        query_remote: str,
        emit,
    ) -> list[SyncDecision]:
        service = SyncService()
        tracks: list[LocalTrack] = []
        count = len(files)
        for index, path in enumerate(files, start=1):
            emit(("log", inspect_file.format(index=index, count=count, name=path.name)))
            local_track = service.inspect([path], options)[0]
            tracks.append(local_track)
            emit(("track", local_track))
            emit(("status", (path, waiting_plan, "")))
            emit(("log", inspect_file_done.format(index=index, count=count, name=path.name)))
        emit(("log", connect_garmin))
        garmin = self._garmin_authenticated(settings)
        emit(("log", query_remote))
        return service.sync_tracks(tracks, garmin, options, on_decision=lambda decision: emit(("decision", decision)))

    def _sync_task(
        self,
        files: list[Path],
        settings: GarminSettings,
        options: SyncOptions,
        queued_template: str,
        connect_garmin: str,
        query_remote: str,
        inspect_file: str,
        inspect_file_done: str,
        emit,
    ) -> list[SyncDecision]:
        service = SyncService()
        count = len(files)
        tracks: list[LocalTrack] = []
        for index, path in enumerate(files, start=1):
            emit(("log", inspect_file.format(index=index, count=count, name=path.name)))
            local_track = service.inspect([path], options)[0]
            tracks.append(local_track)
            emit(("track", local_track))
            emit(("log", queued_template.format(index=index, count=count, name=path.name)))
            emit(("log", inspect_file_done.format(index=index, count=count, name=path.name)))
        emit(("log", connect_garmin))
        garmin = self._garmin_authenticated(settings)
        emit(("log", query_remote))
        return service.sync_tracks(tracks, garmin, options, on_decision=lambda decision: emit(("decision", decision)))

    def _on_sync_done(self, decisions: list[SyncDecision]) -> None:
        message = self.tr("completed_decisions", count=len(decisions))
        self.statusBar().showMessage(message)
        self._append_status_log(message)
        if getattr(self, "_sort_after_sync_done", False):
            self.files_table.sortItems(START_UTC_COLUMN, Qt.AscendingOrder)
            self._sort_after_sync_done = False

    def _on_progress_event(self, event) -> None:
        kind, payload = event
        if kind == "track":
            self._update_track_row(payload)
            self._remember_track(payload)
        elif kind == "decision":
            self._update_decision_row(payload)
            self._append_status_log(_format_decision_log(payload))
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
        self._set_table_values(self.files_table, row, PLAN_COLUMN, [decision.status])
        self._set_table_values(self.files_table, row, NAME_COLUMN, [decision.planned_name])
        self._set_table_values(self.files_table, row, MESSAGE_COLUMN, [decision.message])
        self._set_table_values(self.files_table, row, ACTIVITY_COLUMN, [str(decision.activity_id or "")])
        self.files_table.setSortingEnabled(sorting_enabled)

    def _update_status_row(self, path: Path, plan: str, message: str = "") -> None:
        row = self._row_for_path(path)
        if row is None:
            return
        sorting_enabled = self.files_table.isSortingEnabled()
        self.files_table.setSortingEnabled(False)
        self._set_table_values(self.files_table, row, PLAN_COLUMN, [plan])
        self._set_table_values(self.files_table, row, MESSAGE_COLUMN, [message])
        self.files_table.setSortingEnabled(sorting_enabled)

    def _remember_track(self, track: LocalTrack) -> None:
        self.local_tracks = [
            existing
            for existing in self.local_tracks
            if existing.track_file.source_path != track.track_file.source_path
        ]
        self.local_tracks.append(track)

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

    def _show_error(self, message: str) -> None:
        self.statusBar().showMessage(self.tr("failed"))
        self._append_status_log(message)
        QMessageBox.critical(self, self.tr("error"), message)

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
        for widget in (
            self.files_page,
            self.purge_page,
        ):
            widget.setEnabled(authenticated_idle)

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
            item = QTableWidgetItem(value)
            item.setFlags(item.flags() ^ Qt.ItemIsEditable)
            if source_path is not None and column == FILE_COLUMN:
                item.setData(Qt.UserRole, str(source_path))
                item.setToolTip(str(source_path))
            table.setItem(row, column, item)
        table.setSortingEnabled(sorting_enabled)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._active_tasks:
            QMessageBox.information(self, self.tr("task_running"), self.tr("task_running_text"))
            event.ignore()
            return
        event.accept()


    def _auto_check_session(self) -> None:
        settings = self._garmin_settings()
        self._run_task(
            self.tr("checking_session"),
            lambda: self._status_task(settings),
            lambda user: self._on_auto_session_ok(user),
            on_error=lambda message: self._on_auto_session_failed(message),
        )

    def _on_auto_session_ok(self, user: AuthenticatedUser) -> None:
        self._on_login_ok(user, self.tr("session_usable"))

    def _on_auto_session_failed(self, message: str) -> None:
        self._authenticated = False
        self._refresh_action_state()
        self._append_login_log(message)


def _precheck_has_issues(report: PrecheckReport) -> bool:
    return bool(report.duplicate_groups or report.overlapping_points or report.conflicting_points)


def _format_utc_millis(value) -> str:
    utc_value = value.astimezone(timezone.utc)
    return utc_value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc_value.microsecond // 1000:03d}Z"


def _format_activity_timestamp_ms(value: int | None) -> str:
    if value is None:
        return ""
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _format_decision_log(decision: SyncDecision) -> str:
    details = [decision.source_path.name, decision.status]
    if decision.activity_id is not None:
        details.append(f"activity={decision.activity_id}")
    if decision.message:
        details.append(decision.message)
    return " | ".join(details)


def _timestamped_log_lines(message: str) -> list[str]:
    if not message:
        return []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = message.splitlines() or [message]
    return [f"[{timestamp}] {line}" if line else f"[{timestamp}]" for line in lines]


def _text_column_width(widget: QWidget, sample: str) -> int:
    return widget.fontMetrics().horizontalAdvance(sample) + 32


def _format_precheck(report: PrecheckReport, tr) -> str:
    lines = [
        f"{tr('checked_files')}: {report.checked_count}",
        f"{tr('duplicate_groups')}: {len(report.duplicate_groups)}",
        f"{tr('overlapping_pairs')}: {len(report.overlapping_points)}",
        f"{tr('conflicting_pairs')}: {len(report.conflicting_points)}",
        "",
    ]
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


def _format_purge(summary: PurgeSummary, tr) -> str:
    action = tr("would_delete") if summary.dry_run else tr("deleted")
    lines = [
        f"{tr('date_range')}: {summary.start_date.isoformat()} to {summary.end_date.isoformat()}",
        f"{tr('scanned')}: {summary.scanned_count}",
        f"{tr('matched')}: {summary.matched_count}",
        f"{tr('skipped_unsigned')}: {summary.skipped_unsigned_count}",
        f"{action}: {len(summary.decisions)}",
        "",
    ]
    for decision in summary.decisions:
        lines.append(
            f"{decision.status} activity={decision.activity_id} "
            f"{tr('manufacturer')}={decision.manufacturer} {tr('device_id')}={decision.device_id} "
            f"{decision.activity_name}"
        )
    return "\n".join(lines).rstrip()


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
