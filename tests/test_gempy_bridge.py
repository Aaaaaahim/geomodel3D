"""GemPy 真实引擎端到端验证（需外部/进程内 GemPy 3.x，否则跳过）。"""
from __future__ import annotations

import pytest

from geomodel3d.config.schema import Bounds, InterpConfig, StratigraphyEntry
from geomodel3d.domain.entities import Drillhole, DrillholeInterval
from geomodel3d.engine import EngineRegistry, ModelingSpec
from geomodel3d.engine.gempy_adapter import GemPyEngine, gempy_available

pytestmark = pytest.mark.skipif(not gempy_available(), reason="GemPy 3.x 不可用")


class _Ctx:
    def progress(self, percent: float, stage: str = "") -> None:
        return None

    def raise_if_cancelled(self) -> None:
        return None


def _holes():
    data = [
        ("H1", 0, 0, 120.0, 18.0, 42.0, 68.0),
        ("H2", 180, 40, 132.0, 22.0, 50.0, 75.0),
        ("H3", 60, 180, 118.0, 15.0, 45.0, 72.0),
    ]
    holes = []
    for hole_id, x, y, collar, t1, t2, t3 in data:
        holes.append(
            Drillhole(
                hole_id=hole_id, x=x, y=y, collar_elev=collar, total_depth=t3,
                intervals=[
                    DrillholeInterval(layer_code="A", depth_top=0, depth_bottom=t1),
                    DrillholeInterval(layer_code="B", depth_top=t1, depth_bottom=t2),
                    DrillholeInterval(layer_code="C", depth_top=t2, depth_bottom=t3),
                ],
            )
        )
    return holes


def _spec(engine="gempy"):
    return ModelingSpec(
        bounds=Bounds(min=[0, 0, 0], max=[200, 200, 150]),
        stratigraphy=[
            StratigraphyEntry(layer_code="A", order=0),
            StratigraphyEntry(layer_code="B", order=1),
            StratigraphyEntry(layer_code="C", order=2),
        ],
        drillholes=_holes(),
        resolution=(8, 8, 6),
        interp=InterpConfig(),
        engine=engine,
        purpose="quick_preview",
    )


def test_gempy_builds_umesh():
    mesh = GemPyEngine().build(_spec(), _Ctx())
    assert mesh.n_cells == 8 * 8 * 6
    assert mesh.meta["engine"] == "gempy"
    assert mesh.face_groups
    assert mesh.total_volume() == pytest.approx(200 * 200 * 150, rel=1e-6)


def test_registry_selects_gempy_for_quick_preview():
    engine = EngineRegistry().resolve(_spec(engine="auto"))
    assert engine.name == "gempy"
