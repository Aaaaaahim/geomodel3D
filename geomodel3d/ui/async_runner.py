"""对话框后台任务助手：把 TaskManager 回调安全地投递回 UI 线程。

对话框在 ``enable_tasks=False``（headless/测试）时可直接同步执行；启用任务
管理器时通过本类提交后台任务，避免储量/剖面/统计等计算阻塞界面。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from PyQt6.QtCore import QObject

from geomodel3d.app.tasks import TaskHandle, TaskManager

FinishedCallback = Callable[[Any], None]
FailedCallback = Callable[[BaseException], None]
CancelledCallback = Callable[[], None]


class AsyncRunner(QObject):
    """按 ``run_id`` 过滤 TaskManager 信号并分发给对话框回调。"""

    def __init__(self, tasks: TaskManager, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._tasks = tasks
        self._pending: Dict[
            str, Tuple[FinishedCallback, Optional[FailedCallback], Optional[CancelledCallback]]
        ] = {}
        self._handles: Dict[str, TaskHandle] = {}
        tasks.taskFinished.connect(self._on_finished)
        tasks.taskFailed.connect(self._on_failed)
        tasks.taskCancelled.connect(self._on_cancelled)

    @property
    def busy(self) -> bool:
        return bool(self._pending)

    def submit(
        self,
        fn,
        *,
        name: str,
        on_finished: FinishedCallback,
        on_failed: Optional[FailedCallback] = None,
        on_cancelled: Optional[CancelledCallback] = None,
    ) -> str:
        handle = self._tasks.submit(fn, name=name)
        self._pending[handle.run_id] = (on_finished, on_failed, on_cancelled)
        self._handles[handle.run_id] = handle
        return handle.run_id

    def cancel(self, run_id: str) -> None:
        if run_id in self._handles:
            self._tasks.cancel(run_id)

    def cancel_all(self) -> None:
        for run_id in list(self._handles):
            self._tasks.cancel(run_id)

    def detach(self) -> None:
        for signal, slot in (
            (self._tasks.taskFinished, self._on_finished),
            (self._tasks.taskFailed, self._on_failed),
            (self._tasks.taskCancelled, self._on_cancelled),
        ):
            try:
                signal.disconnect(slot)
            except Exception:  # noqa: BLE001 - 已断开时忽略
                pass
        self._pending.clear()
        self._handles.clear()

    # ------------------------------------------------------------------ #
    def _on_finished(self, run_id: str, result: object) -> None:
        callbacks = self._pending.pop(run_id, None)
        self._handles.pop(run_id, None)
        if callbacks is not None:
            callbacks[0](result)

    def _on_failed(self, run_id: str, exc: object) -> None:
        callbacks = self._pending.pop(run_id, None)
        self._handles.pop(run_id, None)
        if callbacks is not None and callbacks[1] is not None:
            callbacks[1](exc)  # type: ignore[arg-type]

    def _on_cancelled(self, run_id: str) -> None:
        callbacks = self._pending.pop(run_id, None)
        self._handles.pop(run_id, None)
        if callbacks is not None and callbacks[2] is not None:
            callbacks[2]()


__all__ = ["AsyncRunner"]
