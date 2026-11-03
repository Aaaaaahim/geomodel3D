"""局部重建模测试（4.4 / BR-03）。"""
from __future__ import annotations

import numpy as np
import pytest

from geomodel3d.algorithms.geometry import fault_impact_region, region_contains
from geomodel3d.algorithms.local_rebuild import rebuild_local_layered
from geomodel3d.config.schema import Bounds, InterpConfig, StratigraphyEntry
from geomodel3d.domain.entities import Drillhole, DrillholeInterval, Fault, FaultParams
from geomodel3d.engine import ModelingSpec
from geomodel3d.engine.mock import MockEngine


class _Ctx:
    def progress(self, percent: float, stage: str = "") -> None:
        return None

    def raise_if_cancelled(self) -> None:
        return None


def _holes():
    return [
        Drillhole(
            hole_id="H1", x=0, y=0, collar_elev=100, total_depth=80,
            intervals=[
                DrillholeInterval(layer_code="A", depth_top=0, depth_bottom=20),
                DrillholeInterval(layer_code="B", depth_top=20, depth_bottom=50),
                DrillholeInterval(layer_code="C", depth_top=50, depth_bottom=80),
            ],
        ),
        Drillhole(
            hole_id="H2", x=200, y=200, collar_elev=120, total_depth=80,
            intervals=[
                DrillholeInterval(layer_code="A", depth_top=0, depth_bottom=25),
                DrillholeInterval(layer_code="B", depth_top=25, depth_bottom=60),
                DrillholeInterval(layer_code="C", depth_top=60, depth_bottom=80),
            ],
        ),
    ]


def _fault(throw):
    return Fault(
        fault_id="F1",
        trace=[[0, 100, 0], [200, 100, 0]],
        params=FaultParams(length=200, depth_of_influence=200, dip=60, strike=90, throw_v=throw, buffer=40),
    )


def _spec(fault):
    return ModelingSpec(
        bounds=Bounds(min=[0, 0, 0], max=[200, 200, 200]),
        stratigraphy=[
            StratigraphyEntry(layer_code="A", order=0),
            StratigraphyEntry(layer_code="B", order=1),
            StratigraphyEntry(layer_code="C", order=2),
        ],
        drillholes=_holes(),
        faults=[fault],
        resolution=(40, 40, 20),
        interp=InterpConfig(),
        engine="mock",
    )


@pytest.fixture()
def base_and_region():
    fault = _fault(10)
    spec = _spec(fault)
    base = MockEngine().build(spec, _Ctx())
    fault.params.throw_v = 40
    region = fault_impact_region(fault, [spec.bounds.min, spec.bounds.max])
    spec.region = region
    spec.extra = {"fault_id": "F1", "old_throw": 10.0, "transition_band": 40.0}
    return base, spec, region


def test_local_rebuild_only_affects_region(base_and_region):
    base, spec, region = base_and_region
    updated = rebuild_local_layered(base, spec, _Ctx())
    old = np.asarray(base.cell_attr["layer"])
    new = np.asarray(updated.cell_attr["layer"])
    changed = old != new
    centers = base.cell_centers()
    inside = region_contains(region, centers)
    assert changed.any()
    assert int((changed & ~inside).sum()) == 0
    assert int(changed.sum()) < int(inside.sum())


def test_local_rebuild_preserves_geometry(base_and_region):
    base, spec, _ = base_and_region
    updated = rebuild_local_layered(base, spec, _Ctx())
    assert updated.n_cells == base.n_cells
    assert np.allclose(updated.vertices, base.vertices)
    assert updated.total_volume() == pytest.approx(base.total_volume(), rel=1e-9)


def test_local_rebuild_horizontal_throw():
    """局部重建模也应把水平落差应用到影响域内的地层柱查询。"""
    fault = _fault(0)
    spec = _spec(fault)
    spec.resolution = (16, 16, 10)
    base = MockEngine().build(spec, _Ctx())

    fault.params.throw_v = 0
    fault.params.throw_h = 150
    region = fault_impact_region(fault, [spec.bounds.min, spec.bounds.max])
    spec.region = region
    spec.extra = {
        "fault_id": "F1",
        "old_throw": 0.0,
        "old_throw_h": 0.0,
        "transition_band": 40.0,
    }
    updated = rebuild_local_layered(base, spec, _Ctx())
    old = np.asarray(base.cell_attr["layer"])
    new = np.asarray(updated.cell_attr["layer"])
    changed = old != new
    centers = base.cell_centers()
    inside = region_contains(region, centers)
    assert changed.any()
    assert int((changed & ~inside).sum()) == 0
    assert updated.cell_attr["fault_affected"].sum() > 0


def test_local_rebuild_meta_and_groups(base_and_region):
    base, spec, region = base_and_region
    updated = rebuild_local_layered(base, spec, _Ctx())
    info = updated.meta["local_rebuild"]
    assert info["affected"] > 0
    assert info["total"] == base.n_cells
    assert set(updated.face_groups) == {"A", "B", "C"}
    assert sum(len(v) for v in updated.face_groups.values()) == base.n_cells
    assert updated.version == base.version + 1
