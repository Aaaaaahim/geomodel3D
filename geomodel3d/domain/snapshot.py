"""工程版本快照（M9-F3 / 3.5）。

快照保存配置、数据源与统一模型的深拷贝，支持时间轴查看、对比与回滚。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from geomodel3d.config.schema import ProjectConfig
from geomodel3d.domain.entities import Drillhole, Fault, SeismicLayer

if TYPE_CHECKING:  # 避免 domain -> engine 的模块级循环依赖
    from geomodel3d.engine.umesh import UMesh


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _copy_config(config: ProjectConfig) -> ProjectConfig:
    return ProjectConfig.model_validate(config.model_dump())


@dataclass
class ProjectSnapshot:
    """一次工程状态快照。"""

    config: ProjectConfig
    label: str = "快照"
    kind: str = "manual"  # manual / auto / model
    drillholes: List[Drillhole] = field(default_factory=list)
    faults: List[Fault] = field(default_factory=list)
    seismic: List[SeismicLayer] = field(default_factory=list)
    mesh: Optional[UMesh] = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created: str = field(default_factory=_now)
    mesh_digest: str = field(default="", repr=False, compare=False)

    @property
    def mesh_hash(self) -> str:
        if not self.mesh_digest and self.mesh is not None:
            self.mesh_digest = self.mesh.content_hash()
        return self.mesh_digest

    def summary(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "created": self.created,
            "name": self.config.project.name,
            "drillholes": len(self.drillholes),
            "faults": len(self.faults),
            "seismic": len(self.seismic),
            "has_model": self.mesh is not None and self.mesh.n_cells > 0,
            "cells": self.mesh.n_cells if self.mesh is not None else 0,
            "mesh_hash": self.mesh_hash[:16],
            "stratigraphy": len(self.config.stratigraphy),
        }


def capture(
    config: ProjectConfig,
    *,
    label: str = "快照",
    kind: str = "manual",
    drillholes: Optional[List[Drillhole]] = None,
    faults: Optional[List[Fault]] = None,
    seismic: Optional[List[SeismicLayer]] = None,
    mesh: Optional[UMesh] = None,
    copy_mesh: bool = True,
    mesh_digest: Optional[str] = None,
) -> ProjectSnapshot:
    """从当前工程状态深拷贝出快照。

    ``copy_mesh=False`` 用于复用内容完全相同的既有快照网格（由调用方保证），
    避免重复快照对同一网格做全量拷贝；恢复快照时仍会再次拷贝，因此不会
    把快照网格的可变引用暴露给工程。
    """
    mesh_obj = mesh.copy() if (mesh is not None and copy_mesh) else mesh
    digest = mesh_digest
    if digest is None and mesh_obj is not None:
        digest = mesh_obj.content_hash()
    return ProjectSnapshot(
        config=_copy_config(config),
        label=label,
        kind=kind,
        drillholes=[hole.model_copy(deep=True) for hole in (drillholes or [])],
        faults=[fault.model_copy(deep=True) for fault in (faults or [])],
        seismic=[layer.model_copy(deep=True) for layer in (seismic or [])],
        mesh=mesh_obj,
        mesh_digest=digest or "",
    )


__all__ = ["ProjectSnapshot", "capture"]
