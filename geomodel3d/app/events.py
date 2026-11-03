"""事件总线（8.4）。

业务与 UI 之间通过主题 + 载荷解耦：业务发布事件，UI 订阅刷新，
互不直接持有对方引用。发布可来自任意线程，订阅回调需自行保证线程安全；
Qt 界面请通过 :class:`~geomodel3d.app.qt_bridge.QtEventBridge` 转投主线程。
"""
from __future__ import annotations

import fnmatch
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

# 事件主题常量（8.4）
PROJECT_OPENED = "project.opened"
PROJECT_CLOSED = "project.closed"
PROJECT_SAVED = "project.saved"
PROJECT_MODIFIED = "project.modified"
PROJECT_SNAPSHOT = "project.snapshot"
DATA_READY = "data.ready"
DATA_REJECTED = "data.rejected"
RUN_STARTED = "run.started"
RUN_PROGRESS = "run.progress"
RUN_FINISHED = "run.finished"
RUN_FAILED = "run.failed"
MODEL_CHANGED = "model.changed"
FAULT_PARAM_CHANGED = "fault.paramChanged"
RESERVE_READY = "reserve.ready"
RESERVE_SENSITIVITY = "reserve.sensitivity"
SECTION_READY = "section.ready"
STATS_READY = "stats.ready"
SELECTION_CHANGED = "selection.changed"
SETTINGS_CHANGED = "app.settingsChanged"
THEME_CHANGED = "app.themeChanged"
LOG_MESSAGE = "app.log"
ERROR_RAISED = "app.error"

Handler = Callable[["Event"], None]


@dataclass(frozen=True)
class Event:
    """一次事件通知。"""

    topic: str
    payload: Any = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = ""


class EventBus:
    """线程安全的发布/订阅总线，支持 ``*`` 通配订阅。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._handlers: Dict[str, List[Handler]] = {}

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """订阅主题，返回取消订阅的可调用对象。"""
        with self._lock:
            self._handlers.setdefault(topic, []).append(handler)

        def _unsubscribe() -> None:
            self.unsubscribe(topic, handler)

        return _unsubscribe

    def unsubscribe(self, topic: str, handler: Handler) -> None:
        with self._lock:
            handlers = self._handlers.get(topic)
            if handlers and handler in handlers:
                handlers.remove(handler)
                if not handlers:
                    self._handlers.pop(topic, None)

    def publish(self, topic: str, payload: Any = None, *, source: str = "") -> Event:
        """同步派发事件；单个订阅者异常不影响其它订阅者。"""
        event = Event(topic=topic, payload=payload, source=source)
        for handler in self._matching(topic):
            try:
                handler(event)
            except Exception:  # noqa: BLE001 - 隔离订阅者故障
                logger.exception("事件订阅者异常: topic=%s", topic)
        return event

    def _matching(self, topic: str) -> List[Handler]:
        with self._lock:
            matched: List[Handler] = []
            for pattern, handlers in self._handlers.items():
                if pattern == topic or (
                    "*" in pattern and fnmatch.fnmatchcase(topic, pattern)
                ):
                    matched.extend(handlers)
            return list(matched)

    def clear(self) -> None:
        with self._lock:
            self._handlers.clear()


__all__ = [
    "Event",
    "EventBus",
    "Handler",
    "PROJECT_OPENED",
    "PROJECT_CLOSED",
    "PROJECT_SAVED",
    "PROJECT_MODIFIED",
    "PROJECT_SNAPSHOT",
    "DATA_READY",
    "DATA_REJECTED",
    "RUN_STARTED",
    "RUN_PROGRESS",
    "RUN_FINISHED",
    "RUN_FAILED",
    "MODEL_CHANGED",
    "FAULT_PARAM_CHANGED",
    "RESERVE_READY",
    "RESERVE_SENSITIVITY",
    "SECTION_READY",
    "STATS_READY",
    "SELECTION_CHANGED",
    "SETTINGS_CHANGED",
    "THEME_CHANGED",
    "LOG_MESSAGE",
    "ERROR_RAISED",
]
