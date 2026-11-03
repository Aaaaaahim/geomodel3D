"""后台任务管理（3.6 / 8.3）。

统一提交、进度、取消、异常与结果缓存。耗时任务全部进入 QThreadPool，
界面不阻塞；任务可取消，取消后由调用方回滚到最近一致快照。
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

logger = logging.getLogger(__name__)


class TaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    CANCELLED = "cancelled"
    FINISHED = "finished"
    FAILED = "failed"


class TaskCancelled(Exception):
    """任务被用户取消。"""


@dataclass
class TaskContext:
    """传给任务函数的上下文：进度上报与取消检查。"""

    run_id: str
    _progress_cb: Callable[[int, str], None]
    _cancel_event: threading.Event

    def progress(self, percent: float, stage: str = "") -> None:
        self._progress_cb(max(0, min(100, int(percent))), stage)

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise TaskCancelled(self.run_id)


TaskFn = Callable[[TaskContext], Any]


class _WorkerSignals(QObject):
    progress = pyqtSignal(str, int, str)
    finished = pyqtSignal(str, object)
    failed = pyqtSignal(str, object)
    cancelled = pyqtSignal(str)


class _Worker(QRunnable):
    def __init__(self, run_id: str, fn: TaskFn) -> None:
        super().__init__()
        self.run_id = run_id
        self._fn = fn
        self._cancel_event = threading.Event()
        self.signals = _WorkerSignals()
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:  # noqa: D401 - QRunnable 协议
        ctx = TaskContext(
            run_id=self.run_id,
            _progress_cb=lambda pct, stage: self.signals.progress.emit(self.run_id, pct, stage),
            _cancel_event=self._cancel_event,
        )
        try:
            ctx.raise_if_cancelled()
            result = self._fn(ctx)
            if self._cancel_event.is_set():
                self.signals.cancelled.emit(self.run_id)
            else:
                self.signals.finished.emit(self.run_id, result)
        except TaskCancelled:
            self.signals.cancelled.emit(self.run_id)
        except Exception as exc:  # noqa: BLE001 - 统一上报
            logger.exception("后台任务失败: %s", self.run_id)
            self.signals.failed.emit(self.run_id, exc)


@dataclass
class TaskHandle:
    run_id: str
    state: TaskState = TaskState.PENDING
    result: Any = None
    error: Optional[BaseException] = None
    _worker: Optional[_Worker] = field(default=None, repr=False)

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()


class TaskManager(QObject):
    """任务提交入口。所有信号均在发起线程（通常主线程）派发。"""

    taskSubmitted = pyqtSignal(str, str)
    taskProgress = pyqtSignal(str, int, str)
    taskFinished = pyqtSignal(str, object)
    taskFailed = pyqtSignal(str, object)
    taskCancelled = pyqtSignal(str)

    def __init__(self, max_threads: Optional[int] = None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        # 独立线程池，避免影响/受制于全局池中的其它 QRunnable
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(max_threads or max(1, QThreadPool.globalInstance().maxThreadCount() - 1))
        self._handles: Dict[str, TaskHandle] = {}

    def submit(self, fn: TaskFn, *, name: str = "task") -> TaskHandle:
        run_id = f"{name}-{uuid.uuid4().hex[:8]}"
        handle = TaskHandle(run_id=run_id)
        worker = _Worker(run_id, fn)
        handle._worker = worker
        self._handles[run_id] = handle

        worker.signals.progress.connect(self._on_progress)
        worker.signals.finished.connect(self._on_finished)
        worker.signals.failed.connect(self._on_failed)
        worker.signals.cancelled.connect(self._on_cancelled)

        handle.state = TaskState.RUNNING
        self._pool.start(worker)
        self.taskSubmitted.emit(run_id, name)
        logger.info("任务已提交: %s (%s)", run_id, name)
        return handle

    def get(self, run_id: str) -> Optional[TaskHandle]:
        return self._handles.get(run_id)

    def cancel(self, run_id: str) -> None:
        handle = self._handles.get(run_id)
        if handle is not None:
            handle.cancel()

    def cancel_all(self) -> None:
        for handle in list(self._handles.values()):
            handle.cancel()

    def _on_progress(self, run_id: str, percent: int, stage: str) -> None:
        handle = self._handles.get(run_id)
        if handle is not None and handle.state == TaskState.RUNNING:
            self.taskProgress.emit(run_id, percent, stage)

    def _on_finished(self, run_id: str, result: object) -> None:
        handle = self._handles.pop(run_id, None)
        if handle is not None:
            handle.state = TaskState.FINISHED
            handle.result = result
            handle._worker = None
        self.taskFinished.emit(run_id, result)

    def _on_failed(self, run_id: str, exc: object) -> None:
        handle = self._handles.pop(run_id, None)
        if handle is not None:
            handle.state = TaskState.FAILED
            handle.error = exc  # type: ignore[assignment]
            handle._worker = None
        self.taskFailed.emit(run_id, exc)

    def _on_cancelled(self, run_id: str) -> None:
        handle = self._handles.pop(run_id, None)
        if handle is not None:
            handle.state = TaskState.CANCELLED
            handle._worker = None
        self.taskCancelled.emit(run_id)

    def shutdown(self, wait_ms: int = 8000) -> None:
        """取消所有任务并等待线程池收敛；超时则告警（避免带任务关闭导致状态损坏）。"""
        self.cancel_all()
        if not self._pool.waitForDone(wait_ms):
            logger.warning("仍有后台任务未结束，可能有引擎子进程在运行")


__all__ = [
    "TaskState",
    "TaskCancelled",
    "TaskContext",
    "TaskHandle",
    "TaskManager",
]
