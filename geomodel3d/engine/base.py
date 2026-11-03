"""建模引擎适配器基类（1.6 引擎中立 / 3.2 ADR）。

业务只面向本接口，GemPy/OpenGeode 作为可替换适配器实现，
产物统一为 :class:`~geomodel3d.engine.umesh.UMesh`，杜绝厂商锁定。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from geomodel3d.config.schema import Bounds, InterpConfig, StratigraphyEntry
from geomodel3d.domain.entities import Drillhole, Fault
from geomodel3d.engine.umesh import UMesh

ENGINE_API_VERSION = 1


@dataclass
class ModelingSpec:
    """一次建模的完整输入规格（可序列化为参数快照）。"""

    bounds: Bounds
    stratigraphy: List[StratigraphyEntry]
    drillholes: List[Drillhole] = field(default_factory=list)
    faults: List[Fault] = field(default_factory=list)
    seismic: List[Any] = field(default_factory=list)
    resolution: Sequence[int] = (48, 48, 32)
    interp: InterpConfig = field(default_factory=InterpConfig)
    engine: str = "auto"
    purpose: str = "quick_preview"
    need_high_precision: bool = False
    region: Optional[Sequence[float]] = None  # 局部重建模影响域
    extra: Dict[str, Any] = field(default_factory=dict)

    def param_snapshot(self) -> Dict[str, Any]:
        """生成可追溯的参数快照（3.3 ModelRun）。"""
        return {
            "bounds": self.bounds.model_dump(),
            "resolution": list(self.resolution),
            "engine": self.engine,
            "purpose": self.purpose,
            "need_high_precision": self.need_high_precision,
            "interp": self.interp.model_dump(by_alias=True),
            "n_drillholes": len(self.drillholes),
            "n_faults": len(self.faults),
            "n_seismic": len(self.seismic),
            "region": list(self.region) if self.region else None,
        }


@dataclass
class EngineCapabilities:
    """引擎能力声明，用于调度决策与界面提示。"""

    name: str
    high_precision: bool = False
    fault_cut: bool = False
    fault_displacement: bool = False
    closed_solids: bool = False
    smooth_surfaces: bool = False
    available: bool = True


class ModelingEngine(ABC):
    """建模引擎适配器接口。

    插件可声明 ``api_version``（默认当前 API 版本）与 ``min_app_version``；
    注册表会拒绝不兼容插件，避免加载后运行期崩溃。
    """

    name: str = "base"
    api_version: int = ENGINE_API_VERSION
    min_app_version: str = ""
    capabilities = EngineCapabilities(name="base")

    def is_available(self) -> bool:
        """引擎是否可用（依赖是否安装）。"""
        return True

    @abstractmethod
    def build(self, spec: ModelingSpec, ctx) -> UMesh:
        """从规格构建统一模型。``ctx`` 提供 progress()/raise_if_cancelled()。"""

    def rebuild_local(self, mesh: UMesh, spec: ModelingSpec, ctx) -> UMesh:
        """局部重建模；默认实现为整模重建（适配器可覆盖以优化）。"""
        return self.build(spec, ctx)

    def describe(self) -> Dict[str, Any]:
        caps = self.capabilities
        return {
            "name": self.name,
            "available": self.is_available(),
            "high_precision": caps.high_precision,
            "fault_cut": caps.fault_cut,
            "fault_displacement": caps.fault_displacement,
            "closed_solids": caps.closed_solids,
            "smooth_surfaces": caps.smooth_surfaces,
        }


__all__ = ["ModelingSpec", "EngineCapabilities", "ModelingEngine", "ENGINE_API_VERSION"]
