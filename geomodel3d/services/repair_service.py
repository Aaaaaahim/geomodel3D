"""模型修复服务（M6）：体检与自动修复。"""
from __future__ import annotations

import logging
from typing import Optional

from geomodel3d.algorithms.mesh_health import HealthReport, health_check
from geomodel3d.algorithms.repair import RepairOptions, RepairReport, repair
from geomodel3d.app.events import MODEL_CHANGED, EventBus
from geomodel3d.app.tasks import TaskManager
from geomodel3d.config.errors import ErrorCode, GeoModelError
from geomodel3d.services.project_service import ProjectService

logger = logging.getLogger(__name__)


class RepairService:
    """模型体检与修复用例服务。"""

    def __init__(
        self,
        bus: EventBus,
        projects: ProjectService,
        tasks: Optional[TaskManager] = None,
    ) -> None:
        self._bus = bus
        self._projects = projects
        self._tasks = tasks

    def health(self) -> HealthReport:
        project = self._projects._require_project()
        if not project.has_model():
            raise GeoModelError(ErrorCode.RSV_GEO_001, "尚无统一模型，无法体检。")
        return health_check(project.mesh)

    def repair(self, options: Optional[RepairOptions] = None, *, async_run: bool = False):
        """对当前统一模型执行修复并回写工程。

        ``async_run=True`` 且存在任务管理器时进入后台线程，避免大网格修复
        阻塞界面；否则同步执行（headless 测试）。
        """
        if self._tasks is not None and async_run:
            return self._tasks.submit(lambda ctx: self._repair_impl(options, ctx), name="repair")
        return self._repair_impl(options, None)

    def _repair_impl(self, options: Optional[RepairOptions], ctx):
        project = self._projects._require_project()
        if not project.has_model():
            raise GeoModelError(ErrorCode.RSV_GEO_001, "尚无统一模型，无法修复。")
        if ctx is not None:
            ctx.progress(5, "模型体检与修复")
        fixed, report = repair(project.mesh, options)
        if ctx is not None:
            ctx.progress(90, "回写修复结果")
        project.mesh = fixed
        project.mark_dirty()
        self._bus.publish(MODEL_CHANGED, {"version": fixed.version}, source="repair")
        logger.info("模型修复完成: %s", report.summary())
        if ctx is not None:
            ctx.progress(100, "完成")
        return report


__all__ = ["RepairService", "RepairOptions", "RepairReport"]
