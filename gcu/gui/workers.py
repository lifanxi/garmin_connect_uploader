from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(object)
    finished = Signal()


class TaskWorker(QRunnable):
    def __init__(self, task: Callable[..., Any], pass_progress: bool = False):
        super().__init__()
        self.setAutoDelete(False)
        self.task = task
        self.pass_progress = pass_progress
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            if self.pass_progress:
                self.signals.result.emit(self.task(self.signals.progress.emit))
            else:
                self.signals.result.emit(self.task())
        except BaseException as exc:  # pragma: no cover - Qt thread boundary
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()
