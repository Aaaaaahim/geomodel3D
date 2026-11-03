"""事件总线 -> Qt 信号桥（主线程安全投递）。

后台线程调用 :meth:`EventBus.publish` 时，桥接对象以 Qt 信号重新广播；
由于接收者位于主线程，Qt 自动采用队列连接，从而安全更新界面。
"""
from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from geomodel3d.app.events import Event, EventBus


class QtEventBridge(QObject):
    """把总线事件转成 Qt 信号 ``event(topic, payload)``。"""

    event = pyqtSignal(str, object)

    def __init__(self, bus: EventBus, topics: Optional[List[str]] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._bus = bus
        self._unsubscribers = []
        for topic in topics or ["*"]:
            self._unsubscribers.append(bus.subscribe(topic, self._relay))

    def _relay(self, event: Event) -> None:
        self.event.emit(event.topic, event.payload)

    def detach(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()


__all__ = ["QtEventBridge"]
