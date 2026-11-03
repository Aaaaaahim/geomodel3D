"""断层参数编辑服务（M3 / 4.4）。

编辑参数 -> 计算影响域 -> 触发局部重建模 -> 入 Undo 栈。
影响域外零改动；每次编辑生成一条 ModelRun 记录（参数快照+重算范围）。
"""
from __future__ import annotations

import logging
from typing import Any

from geomodel3d.algorithms.fault_cut import cut_mesh_by_fault
from geomodel3d.algorithms.geometry import fault_impact_region
from geomodel3d.app.events import FAULT_PARAM_CHANGED, MODEL_CHANGED, EventBus
from geomodel3d.config.errors import ErrorCode, GeoModelError
from geomodel3d.domain.entities import Fault, FaultParams, FaultSide
from geomodel3d.services.modeling_service import ModelingService, spec_from_project
from geomodel3d.services.project_service import ProjectService

logger = logging.getLogger(__name__)


class FaultEditorService:
    """断层参数化编辑与局部重建模。"""

    def __init__(
        self,
        bus: EventBus,
        projects: ProjectService,
        modeling: ModelingService,
    ) -> None:
        self._bus = bus
        self._projects = projects
        self._modeling = modeling

    def preview_impact(self, fault_id: str):
        """返回断层影响域包围盒（不重建模）。"""
        project = self._projects._require_project()
        fault = self._find(fault_id)
        return fault_impact_region(fault, project.bounds_list())

    def create_fault(self, name: str = "新建断层", **overrides) -> Fault:
        """在工程中部新建一条默认走向的断层（便于手动编辑）。"""
        project = self._projects._require_project()
        (x0, y0, z0), (x1, y1, z1) = project.bounds_list()
        ymid = (y0 + y1) / 2.0
        params = FaultParams(
            length=float(x1 - x0),
            depth_of_influence=float(z1 - z0) if z1 > z0 else 500.0,
            dip=60.0,
            strike=90.0,
            throw_v=10.0,
            buffer=50.0,
        )
        if overrides:
            params = params.model_copy(update=overrides)
        fault = Fault(
            fault_id=f"F{len(project.faults) + 1}",
            name=name,
            trace=[[float(x0), float(ymid), 0.0], [float(x1), float(ymid), 0.0]],
            params=params,
            side=FaultSide.NORMAL,
        )
        project.faults.append(fault)
        project.mark_dirty()
        logger.info("新建断层: %s", fault.fault_id)
        return fault

    def update_fault(
        self,
        fault_id: str,
        params: FaultParams,
        *,
        commit: bool = True,
        async_run: bool = True,
        resolution=(48, 48, 32),
    ) -> Any:
        """更新断层参数；``commit=True`` 触发局部重建模。"""
        project = self._projects._require_project()
        fault = self._find(fault_id)
        old_params = fault.params.model_copy(deep=True)
        fault.params = params
        project.push_undo(
            {
                "kind": "fault_params",
                "fault_id": fault_id,
                "old": old_params.model_dump(),
                "new": params.model_dump(),
            }
        )
        project.mark_dirty()

        region = fault_impact_region(fault, project.bounds_list())
        self._bus.publish(
            FAULT_PARAM_CHANGED,
            {"fault_id": fault_id, "params": params.model_dump(), "region": region},
            source="fault_editor",
        )
        logger.info("断层参数更新: %s region=%s", fault_id, region)

        if not commit or project.mesh is None:
            return region
        spec = spec_from_project(
            project,
            engine=project.config.engine_policy,
            purpose="fault_edit",
            resolution=resolution,
            region=region,
        )
        spec.extra["fault_id"] = fault_id
        spec.extra["old_throw"] = float(old_params.throw_v)
        spec.extra["old_throw_h"] = float(old_params.throw_h)
        spec.extra["transition_band"] = max(float(params.buffer), 1.0)
        return self._modeling.rebuild_local(spec, region, async_run=async_run)

    def cut_fault(self, fault_id: str):
        """沿断层做 VTK 几何切割（两盘封闭，非 OpenGeode 原生布尔）。

        切割前自动建立版本快照，便于通过版本历史回滚。
        """
        project = self._projects._require_project()
        if not project.has_model():
            raise GeoModelError(
                ErrorCode.GEN_STATE_001, "尚无统一模型，无法执行断层切割。"
            )
        fault = self._find(fault_id)
        try:
            self._projects.snapshot("断层切割前", kind="model")
        except Exception:  # noqa: BLE001 - 快照失败不阻断切割
            logger.warning("断层切割前快照失败", exc_info=True)

        mesh = cut_mesh_by_fault(project.mesh, fault)
        project.mesh = mesh
        project.mark_dirty()
        self._bus.publish(
            MODEL_CHANGED,
            {"version": mesh.version, "fault_cut": fault_id},
            source="fault_editor",
        )
        logger.info("断层几何切割预览: %s (%d 单元)", fault_id, mesh.n_cells)
        return mesh

    def undo(self) -> bool:
        project = self._projects._require_project()
        state = project.undo()
        if state is None:
            return False
        if state.get("kind") == "fault_params":
            self._restore_and_rebuild(state["fault_id"], FaultParams.model_validate(state["old"]))
            logger.info("撤销断层参数: %s", state["fault_id"])
        return True

    def redo(self) -> bool:
        project = self._projects._require_project()
        state = project.redo()
        if state is None:
            return False
        if state.get("kind") == "fault_params":
            self._restore_and_rebuild(state["fault_id"], FaultParams.model_validate(state["new"]))
        return True

    def _restore_and_rebuild(self, fault_id: str, params: FaultParams) -> None:
        """恢复参数并触发局部重建模，使模型与参数保持一致。"""
        project = self._projects._require_project()
        fault = self._find(fault_id)
        old_throw = float(fault.params.throw_v)
        old_throw_h = float(fault.params.throw_h)
        fault.params = params
        project.mark_dirty()
        if project.mesh is None:
            return
        region = fault_impact_region(fault, project.bounds_list())
        spec = spec_from_project(
            project,
            engine=project.config.engine_policy,
            purpose="fault_edit",
            region=region,
        )
        spec.extra["fault_id"] = fault_id
        spec.extra["old_throw"] = old_throw
        spec.extra["old_throw_h"] = old_throw_h
        spec.extra["transition_band"] = max(float(params.buffer), 1.0)
        self._modeling.rebuild_local(spec, region, async_run=True)

    def _find(self, fault_id: str):
        project = self._projects._require_project()
        for fault in project.faults:
            if fault.fault_id == fault_id:
                return fault
        raise GeoModelError(ErrorCode.GEN_STATE_001, f"未找到断层: {fault_id}")


__all__ = ["FaultEditorService"]
