"""建模服务（M2 / 4.4）：创建运行、取消、局部重建模、精度升级。

耗时构建通过 :class:`~geomodel3d.app.tasks.TaskManager` 进入后台线程；
无任务管理器时同步执行，便于 headless 测试与批处理。
"""
from __future__ import annotations

import logging
import time
from typing import Any, List, Optional

from geomodel3d.app.events import (
    MODEL_CHANGED,
    RUN_FAILED,
    RUN_FINISHED,
    RUN_PROGRESS,
    RUN_STARTED,
    EventBus,
)
from geomodel3d.app.tasks import TaskManager
from geomodel3d.algorithms.geometry import fault_impact_region
from geomodel3d.config.errors import ErrorCode, GeoModelError
from geomodel3d.domain.entities import EngineKind, ModelRunMeta, RunStatus
from geomodel3d.engine.base import ModelingEngine, ModelingSpec
from geomodel3d.engine.registry import EngineRegistry
from geomodel3d.engine.umesh import UMesh
from geomodel3d.services.project_service import ProjectService

logger = logging.getLogger(__name__)


def spec_from_project(
    project,
    *,
    engine: str = "auto",
    purpose: str = "quick_preview",
    resolution=(48, 48, 32),
    region=None,
) -> ModelingSpec:
    """由当前工程装配建模规格（复制可变输入，避免后台线程与主线程共享状态）。"""
    return ModelingSpec(
        bounds=project.config.project.bounds.model_copy(deep=True),
        stratigraphy=[entry.model_copy(deep=True) for entry in project.config.stratigraphy],
        drillholes=[hole.model_copy(deep=True) for hole in project.drillholes],
        faults=[fault.model_copy(deep=True) for fault in project.faults],
        seismic=[layer.model_copy(deep=True) for layer in project.seismic],
        resolution=resolution,
        interp=project.config.interp.model_copy(deep=True),
        engine=engine,
        purpose=purpose,
        region=region,
    )


class ModelingService:
    """建模编排用例服务。"""

    def __init__(
        self,
        bus: EventBus,
        projects: ProjectService,
        registry: Optional[EngineRegistry] = None,
        tasks: Optional[TaskManager] = None,
    ) -> None:
        self._bus = bus
        self._projects = projects
        self._registry = registry or EngineRegistry()
        self._tasks = tasks

    # ------------------------------------------------------------------ #
    def create_run(
        self,
        spec: ModelingSpec,
        *,
        async_run: bool = True,
        engine_name: Optional[str] = None,
    ) -> Any:
        """提交一次建模。返回 :class:`TaskHandle`（异步）或 :class:`ModelRunMeta`（同步）。"""
        if engine_name:
            engine = self._registry.get(engine_name)
        elif not spec.stratigraphy:
            engine = self._registry.get("mock")
        else:
            engine = self._registry.resolve(spec)
        meta = ModelRunMeta(
            engine=_engine_kind(engine),
            status=RunStatus.RUNNING,
            params_snapshot=spec.param_snapshot(),
            region=list(spec.region) if spec.region else None,
        )
        project = self._projects.project
        if project is not None:
            self._snapshot_before_run(project)
            project.add_run(meta)
        self._bus.publish(RUN_STARTED, {"run_id": meta.run_id, "engine": engine.name}, source="modeling")

        if self._tasks is not None and async_run:
            handle = self._tasks.submit(
                lambda task_ctx: self._execute(
                    engine, spec, meta, _BridgeContext(self._bus, meta.run_id, task_ctx)
                ),
                name="modeling",
            )
            return handle
        return self._execute(engine, spec, meta, _SyncContext(self._bus, meta.run_id), async_task=False)

    def rebuild_local(self, spec: ModelingSpec, region, *, async_run: bool = True) -> Any:
        """局部重建模（4.4）：仅重算影响域。"""
        spec.region = list(region)
        spec.extra["target"] = "rebuild"
        engine = self._registry.resolve(spec)
        meta = ModelRunMeta(
            engine=_engine_kind(engine),
            status=RunStatus.RUNNING,
            params_snapshot=spec.param_snapshot(),
            region=list(region),
        )
        project = self._projects.project
        if project is not None:
            self._snapshot_before_run(project)
            project.add_run(meta)
        self._bus.publish(
            RUN_STARTED, {"run_id": meta.run_id, "engine": engine.name, "local": True}, source="modeling"
        )
        base_mesh = project.mesh if project else None

        def _task(ctx, *, async_task: bool = True):
            if base_mesh is not None:
                return self._execute_local(engine, base_mesh, spec, meta, ctx, async_task=async_task)
            return self._execute(engine, spec, meta, ctx, async_task=async_task)

        if self._tasks is not None and async_run:
            return self._tasks.submit(
                lambda task_ctx: _task(_BridgeContext(self._bus, meta.run_id, task_ctx)),
                name="rebuild",
            )
        return _task(_SyncContext(self._bus, meta.run_id), async_task=False)

    def upgrade_precision(self, *, resolution=(64, 64, 48), async_run: bool = True) -> Any:
        """快速预览 -> 高精度升级（4.3.2 无摩擦路径）。"""
        project = self._projects._require_project()
        spec = spec_from_project(
            project,
            engine=EngineKind.OPENGEODE.value,
            purpose="high_precision",
            resolution=resolution,
        )
        spec.need_high_precision = True
        spec.extra["target"] = "export"
        return self.create_run(spec, async_run=async_run, engine_name=EngineKind.OPENGEODE.value)

    def fault_region(self, fault_id: str):
        project = self._projects._require_project()
        for fault in project.faults:
            if fault.fault_id == fault_id:
                return fault_impact_region(fault, project.bounds_list())
        raise GeoModelError(
            ErrorCode.GEN_STATE_001,
            f"未找到断层: {fault_id}",
        )

    def list_runs(self) -> List[ModelRunMeta]:
        project = self._projects.project
        return list(project.runs) if project else []

    # ------------------------------------------------------------------ #
    def _execute(
        self,
        engine: ModelingEngine,
        spec: ModelingSpec,
        meta: ModelRunMeta,
        ctx,
        *,
        async_task: bool = True,
    ) -> ModelRunMeta:
        started = time.perf_counter()
        try:
            mesh = self._build_with_fallback(engine, spec, ctx, meta)
        except Exception as exc:  # noqa: BLE001
            meta.status = RunStatus.FAILED
            meta.message = str(exc)
            self._bus.publish(
                RUN_FAILED,
                {
                    "run_id": meta.run_id,
                    "error": str(exc),
                    "handled_by_caller": not async_task,
                },
                source="modeling",
            )
            logger.exception("建模失败: %s", meta.run_id)
            raise
        meta.status = RunStatus.FINISHED
        meta.duration_s = round(time.perf_counter() - started, 3)
        self._commit(mesh, meta)
        return meta

    def _build_with_fallback(self, engine: ModelingEngine, spec: ModelingSpec, ctx, meta: ModelRunMeta):
        """所选引擎不可用时回退 Mock；其它异常（潜在缺陷）应暴露为失败。"""
        from geomodel3d.app.tasks import TaskCancelled
        from geomodel3d.config.errors import EngineUnavailableError

        try:
            return engine.build(spec, ctx)
        except (TaskCancelled, EngineUnavailableError) as exc:
            if engine.name == "mock" or isinstance(exc, TaskCancelled):
                raise
            logger.warning("引擎 %s 不可用，回退 Mock：%s", engine.name, exc)
            meta.engine = EngineKind.MOCK
            meta.message = f"已回退 Mock：{exc.message}"
            return self._registry.get("mock").build(spec, ctx)

    def _execute_local(
        self,
        engine: ModelingEngine,
        base: UMesh,
        spec: ModelingSpec,
        meta: ModelRunMeta,
        ctx,
        *,
        async_task: bool = True,
    ) -> ModelRunMeta:
        from geomodel3d.app.tasks import TaskCancelled
        from geomodel3d.config.errors import EngineUnavailableError

        started = time.perf_counter()
        try:
            try:
                mesh = engine.rebuild_local(base, spec, ctx)
            except (TaskCancelled, EngineUnavailableError) as exc:
                if engine.name == "mock" or isinstance(exc, TaskCancelled):
                    raise
                logger.warning("引擎 %s 局部重建模不可用，回退 Mock：%s", engine.name, exc)
                meta.engine = EngineKind.MOCK
                meta.message = f"已回退 Mock：{exc.message}"
                mesh = self._registry.get("mock").rebuild_local(base, spec, ctx)
        except Exception as exc:  # noqa: BLE001
            meta.status = RunStatus.FAILED
            meta.message = str(exc)
            self._bus.publish(
                RUN_FAILED,
                {
                    "run_id": meta.run_id,
                    "error": str(exc),
                    "handled_by_caller": not async_task,
                },
                source="modeling",
            )
            raise
        meta.status = RunStatus.FINISHED
        meta.duration_s = round(time.perf_counter() - started, 3)
        self._commit(mesh, meta)
        return meta

    def _snapshot_before_run(self, project) -> None:
        """长任务开始前自动建立版本快照，供失败/取消后回滚（3.6 / BR-08）。"""
        if not project.has_model():
            return
        try:
            self._projects.snapshot("任务前自动快照", kind="model")
        except Exception:  # noqa: BLE001 - 快照失败不应阻断建模
            logger.warning("建模前自动快照失败", exc_info=True)

    def _commit(self, mesh: UMesh, meta: ModelRunMeta) -> None:
        project = self._projects.project
        if project is not None:
            mesh.version = (project.mesh.version + 1) if project.mesh is not None else 1
            project.mesh = mesh
            project.mark_dirty()
        self._bus.publish(RUN_PROGRESS, {"run_id": meta.run_id, "percent": 100, "stage": "完成"}, source="modeling")
        self._bus.publish(
            RUN_FINISHED,
            {"run_id": meta.run_id, "mesh": mesh, "duration_s": meta.duration_s},
            source="modeling",
        )
        self._bus.publish(MODEL_CHANGED, {"version": mesh.version}, source="modeling")
        logger.info("建模完成: %s (%d 单元, %.3fs)", meta.run_id, mesh.n_cells, meta.duration_s)


class _SyncContext:
    """同步执行上下文：把进度直接转发到事件总线。"""

    def __init__(self, bus: EventBus, run_id: str) -> None:
        self._bus = bus
        self.run_id = run_id

    def progress(self, percent: float, stage: str = "") -> None:
        self._bus.publish(
            RUN_PROGRESS,
            {"run_id": self.run_id, "percent": int(percent), "stage": stage},
            source="modeling",
        )

    def raise_if_cancelled(self) -> None:
        return None


class _BridgeContext:
    """异步执行上下文：进度同时转发到任务信号与事件总线。"""

    def __init__(self, bus: EventBus, run_id: str, inner) -> None:
        self._bus = bus
        self.run_id = run_id
        self._inner = inner

    def progress(self, percent: float, stage: str = "") -> None:
        self._bus.publish(
            RUN_PROGRESS,
            {"run_id": self.run_id, "percent": int(percent), "stage": stage},
            source="modeling",
        )
        self._inner.progress(percent, stage)

    def raise_if_cancelled(self) -> None:
        self._inner.raise_if_cancelled()


def _engine_kind(engine: ModelingEngine) -> EngineKind:
    try:
        return EngineKind(engine.name)
    except ValueError:
        return EngineKind.MOCK


__all__ = ["ModelingService", "spec_from_project"]
