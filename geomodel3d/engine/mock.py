"""Mock 引擎：无需 GemPy/OpenGeode 即可产出分层体网格。

分层建模核心位于 :mod:`geomodel3d.engine.layered`，本引擎仅作为
引擎适配器封装，用于骨架阶段联调、演示与单元测试。
"""
from __future__ import annotations

from geomodel3d.engine.base import EngineCapabilities, ModelingEngine, ModelingSpec
from geomodel3d.engine.layered import build_layered_mesh
from geomodel3d.engine.umesh import UMesh


class MockEngine(ModelingEngine):
    """分层体元建模引擎（纯 NumPy 实现）。"""

    name = "mock"
    capabilities = EngineCapabilities(
        name="mock",
        high_precision=False,
        fault_cut=False,
        fault_displacement=True,
        closed_solids=True,
        smooth_surfaces=False,
        available=True,
    )

    def build(self, spec: ModelingSpec, ctx) -> UMesh:
        return build_layered_mesh(spec, ctx, engine_name=self.name)

    def rebuild_local(self, mesh: UMesh, spec: ModelingSpec, ctx) -> UMesh:
        if spec.region is None:
            return self.build(spec, ctx)
        from geomodel3d.algorithms.local_rebuild import rebuild_local_layered

        return rebuild_local_layered(mesh, spec, ctx)


__all__ = ["MockEngine"]
