"""应用上下文：依赖装配（3.1 / 9.1 app 层）。

集中创建事件总线、任务管理器、引擎注册表与各业务服务，
供 UI 通过单一入口访问，避免全局单例与跨层直接引用。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from geomodel3d.app.events import THEME_CHANGED, EventBus
from geomodel3d.app.logging_setup import ExceptionHandler, setup_logging
from geomodel3d.app.tasks import TaskManager
from geomodel3d.config.defaults import user_data_dir
from geomodel3d.config.settings_store import load_settings, save_settings
from geomodel3d.config.schema import AppSettings
from geomodel3d.engine.registry import EngineRegistry
from geomodel3d.services.data_service import DataService
from geomodel3d.services.export_service import ExportService
from geomodel3d.services.fault_editor_service import FaultEditorService
from geomodel3d.services.modeling_service import ModelingService
from geomodel3d.services.project_service import ProjectService
from geomodel3d.services.repair_service import RepairService
from geomodel3d.services.reserve_service import ReserveService
from geomodel3d.services.section_service import SectionService
from geomodel3d.services.stats_service import StatsService

logger = logging.getLogger(__name__)


class AppContext:
    """应用级依赖容器。"""

    def __init__(
        self,
        settings: Optional[AppSettings] = None,
        *,
        log_dir: Optional[Path] = None,
        enable_tasks: bool = True,
    ) -> None:
        self.settings = settings or load_settings()
        self.bus = EventBus()
        self.log_file = setup_logging(
            log_dir or (user_data_dir() / "logs"),
            self.settings.log_level,
            bus=self.bus,
        )
        self.exceptions = ExceptionHandler(self.bus)
        self.tasks = TaskManager() if enable_tasks else None
        self.registry = EngineRegistry()

        self.projects = ProjectService(self.bus, self.settings)
        self.data = DataService(self.bus, self.projects)
        self.modeling = ModelingService(self.bus, self.projects, self.registry, self.tasks)
        self.fault_editor = FaultEditorService(self.bus, self.projects, self.modeling)
        self.reserve = ReserveService(self.bus, self.projects)
        self.section = SectionService(self.bus, self.projects)
        self.exporter = ExportService(self.bus, self.projects)
        self.repair = RepairService(self.bus, self.projects, self.tasks)
        self.stats = StatsService(self.bus, self.projects, self.data)

    # ------------------------------------------------------------------ #
    def crash_dir(self) -> Path:
        """崩溃转储目录（Windows minidump / faulthandler 日志）。"""
        return user_data_dir() / "crashes"

    def set_theme(self, theme: str) -> None:
        self.settings.theme = theme
        self.bus.publish(THEME_CHANGED, theme, source="context")

    def persist_settings(self) -> None:
        save_settings(self.settings)

    def shutdown(self) -> None:
        if self.tasks is not None:
            self.tasks.shutdown()
        self.persist_settings()
        logger.info("应用上下文已关闭")


__all__ = ["AppContext"]
