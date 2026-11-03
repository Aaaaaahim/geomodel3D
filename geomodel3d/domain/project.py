"""工程根对象（3.3 / 3.5）。

承载工程配置、三类数据源、统一模型与建模运行记录，并维护脏标记
与撤销/重做历史。序列化由 :mod:`geomodel3d.dataio.project_file` 负责。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from geomodel3d.config.schema import ProjectConfig
from geomodel3d.domain.entities import Drillhole, Fault, ModelRunMeta, SeismicLayer
from geomodel3d.domain.snapshot import ProjectSnapshot, capture

if TYPE_CHECKING:  # 避免 domain -> engine 的模块级循环依赖
    from geomodel3d.engine.umesh import UMesh


class Project:
    """运行期工程状态。"""

    def __init__(
        self,
        config: ProjectConfig,
        *,
        path: Optional[Path] = None,
        drillholes: Optional[List[Drillhole]] = None,
        faults: Optional[List[Fault]] = None,
        seismic: Optional[List[SeismicLayer]] = None,
    ) -> None:
        self.config = config
        self.path = Path(path) if path else None
        self.drillholes: List[Drillhole] = list(drillholes or [])
        self.faults: List[Fault] = list(faults or [])
        self.seismic: List[SeismicLayer] = list(seismic or [])
        self.mesh: Optional[UMesh] = None
        self.runs: List[ModelRunMeta] = []
        self.snapshots: List[ProjectSnapshot] = []
        self.dirty: bool = False
        self._undo: List[Any] = []
        self._redo: List[Any] = []

    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return self.config.project.name

    @property
    def crs(self):
        return self.config.project.crs

    def bounds_list(self) -> List[List[float]]:
        bounds = self.config.project.bounds
        return [list(bounds.min), list(bounds.max)]

    def mark_dirty(self) -> None:
        self.dirty = True

    def mark_clean(self) -> None:
        self.dirty = False

    def has_model(self) -> bool:
        return self.mesh is not None and self.mesh.n_cells > 0

    # 撤销 / 重做 ------------------------------------------------------- #
    def push_undo(self, snapshot: Any) -> None:
        self._undo.append(snapshot)
        self._redo.clear()

    def undo(self) -> Optional[Any]:
        if not self._undo:
            return None
        state = self._undo.pop()
        self._redo.append(state)
        return state

    def redo(self) -> Optional[Any]:
        if not self._redo:
            return None
        state = self._redo.pop()
        self._undo.append(state)
        return state

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def add_run(self, run: ModelRunMeta) -> None:
        self.runs.append(run)
        self.mark_dirty()

    # 版本快照 --------------------------------------------------------- #
    def create_snapshot(
        self, label: str = "快照", *, kind: str = "manual", versions_max: int = 20
    ) -> ProjectSnapshot:
        mesh = self.mesh
        copy_mesh = True
        digest = ""
        if mesh is not None:
            digest = mesh.content_hash()
            for existing in reversed(self.snapshots):
                if (
                    existing.mesh is not None
                    and existing.mesh_hash == digest
                    and existing.mesh.version == mesh.version
                    and existing.mesh.meta == mesh.meta
                    and existing.mesh.face_groups == mesh.face_groups
                ):
                    mesh = existing.mesh
                    copy_mesh = False
                    break
        snapshot = capture(
            self.config,
            label=label,
            kind=kind,
            drillholes=self.drillholes,
            faults=self.faults,
            seismic=self.seismic,
            mesh=mesh,
            copy_mesh=copy_mesh,
            mesh_digest=digest or None,
        )
        self.snapshots.append(snapshot)
        if versions_max > 0:
            self.snapshots = self.snapshots[-versions_max:]
        return snapshot

    def snapshot(self, snapshot_id: str) -> Optional[ProjectSnapshot]:
        for item in self.snapshots:
            if item.id == snapshot_id:
                return item
        return None

    def restore_snapshot(self, snapshot_id: str) -> bool:
        snapshot = self.snapshot(snapshot_id)
        if snapshot is None:
            return False
        self.config = snapshot.config.model_copy(deep=True)
        self.drillholes = [hole.model_copy(deep=True) for hole in snapshot.drillholes]
        self.faults = [fault.model_copy(deep=True) for fault in snapshot.faults]
        self.seismic = [layer.model_copy(deep=True) for layer in snapshot.seismic]
        self.mesh = snapshot.mesh.copy() if snapshot.mesh is not None else None
        self.mark_dirty()
        return True

    def snapshot_summaries(self) -> List[Dict[str, Any]]:
        return [item.summary() for item in self.snapshots]

    def summary(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path) if self.path else "",
            "dirty": self.dirty,
            "drillholes": len(self.drillholes),
            "faults": len(self.faults),
            "seismic": len(self.seismic),
            "has_model": self.has_model(),
            "cells": self.mesh.n_cells if self.mesh else 0,
            "runs": len(self.runs),
            "crs_epsg": self.crs.epsg,
        }


__all__ = ["Project"]
