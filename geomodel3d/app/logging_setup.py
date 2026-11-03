"""日志与异常处理（8.7）。

- 结构化 JSONL 落盘（可被 SIEM/审计导入）；
- 可选将日志发布到事件总线，供界面日志面板分级着色显示；
- :class:`ExceptionHandler` 将任意异常统一转错误事件，附带错误码与恢复建议。
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import traceback
from pathlib import Path
from typing import Any, List, Optional

from geomodel3d.app.events import ERROR_RAISED, LOG_MESSAGE, EventBus
from geomodel3d.config.errors import ErrorCode, GeoModelError

LOG_FILENAME = "modeling.log"

# 本模块安装的处理器（重复初始化时只清理自己安装的，避免影响其它日志配置）
_MANAGED_HANDLERS: List[logging.Handler] = []


class JsonlFormatter(logging.Formatter):
    """把日志记录序列化为单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("code", "context", "suggestion", "run_id"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class EventBusLogHandler(logging.Handler):
    """把日志发布为 ``app.log`` 事件（界面订阅）。"""

    def __init__(self, bus: EventBus, level: int = logging.INFO) -> None:
        super().__init__(level)
        self._bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._bus.publish(
                LOG_MESSAGE,
                {
                    "level": record.levelname,
                    "message": record.getMessage(),
                    "logger": record.name,
                    "time": self.formatTime(record, "%H:%M:%S"),
                },
                source="logging",
            )
        except Exception:  # noqa: BLE001 - 日志处理器绝不能抛出
            pass


def setup_logging(
    log_dir: Path,
    level: str = "INFO",
    *,
    bus: Optional[EventBus] = None,
    console: bool = True,
    max_bytes: int = 5 * 1024 * 1024,
    backups: int = 3,
) -> Path:
    """初始化根日志。返回日志文件路径。"""
    global _MANAGED_HANDLERS
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / LOG_FILENAME

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    # 只移除/关闭本模块此前安装的处理器，避免影响其它日志配置
    for handler in list(_MANAGED_HANDLERS):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass
    _MANAGED_HANDLERS = []

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(JsonlFormatter())
    root.addHandler(file_handler)
    _MANAGED_HANDLERS.append(file_handler)

    if console:
        stream = logging.StreamHandler()
        stream.setLevel(getattr(logging, level.upper(), logging.INFO))
        stream.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s", "%H:%M:%S")
        )
        root.addHandler(stream)
        _MANAGED_HANDLERS.append(stream)

    if bus is not None:
        bus_handler = EventBusLogHandler(bus, getattr(logging, level.upper(), logging.INFO))
        bus_handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(bus_handler)
        _MANAGED_HANDLERS.append(bus_handler)

    from geomodel3d.app.crash_dump import enable_faulthandler

    enable_faulthandler(log_dir)
    logging.getLogger(__name__).info("日志已初始化: %s", log_file)
    return log_file


class ExceptionHandler:
    """把异常统一转换为错误事件（含错误码与恢复建议）。"""

    def __init__(self, bus: Optional[EventBus] = None) -> None:
        self._bus = bus

    def report(
        self, exc: BaseException, *, context: Optional[dict] = None, publish: bool = True
    ) -> GeoModelError:
        """归一化异常并（可选）发布 ``app.error``。返回规范化后的业务异常。"""
        if isinstance(exc, GeoModelError):
            error = exc
            if context:
                error.context.setdefault("context", context)
        else:
            error = GeoModelError(
                code=ErrorCode.GEN_IO_001,
                message=f"未预期异常: {exc}",
                context={"type": type(exc).__name__, **(context or {})},
            )
        logging.getLogger("geomodel3d").error(
            "%s",
            error.message,
            extra={
                "code": error.code.value,
                "context": error.context,
                "suggestion": error.suggestion,
            },
            exc_info=not isinstance(exc, GeoModelError),
        )
        if publish and self._bus is not None:
            self._bus.publish(ERROR_RAISED, error.as_dict(), source="exception")
        return error

    def traceback_text(self, exc: BaseException) -> str:
        return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


__all__ = [
    "LOG_FILENAME",
    "JsonlFormatter",
    "EventBusLogHandler",
    "setup_logging",
    "ExceptionHandler",
]
