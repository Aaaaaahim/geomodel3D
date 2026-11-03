"""数据回归基准（9.3）：固定输入 -> 固定输出，守护算法行为不漂移。"""
from __future__ import annotations

import pytest

from geomodel3d.algorithms.reserve import estimate_reserves
from geomodel3d.algorithms.section import build_section, make_horizontal_section
from geomodel3d.config.schema import Bounds, InterpConfig, StratigraphyEntry
from geomodel3d.domain.entities import Drillhole, DrillholeInterval
from geomodel3d.domain.reserve import ReserveMetrics
from geomodel3d.engine import EngineRegistry, ModelingSpec

GOLDEN_HASH = "3d9ecfdaff75779edd1a8f9a3623d5ef9f783f66d821b684449e3ecc2616dee3"
GOLDEN_VOLUME = 8_000_000.0
GOLDEN_TONNAGE = 2_738_125.0
GOLDEN_METAL = 52_987.80013976346


class _Ctx:
    def progress(self, percent: float, stage: str = "") -> None:
        return None

    def raise_if_cancelled(self) -> None:
        return None


def _fixed_holes():
    return [
        Drillhole(
            hole_id="H1", x=0, y=0, collar_elev=100, total_depth=80,
            intervals=[
                DrillholeInterval(layer_code="A", depth_top=0, depth_bottom=20, grade=1.2),
                DrillholeInterval(layer_code="B", depth_top=20, depth_bottom=50, grade=2.4),
                DrillholeInterval(layer_code="C", depth_top=50, depth_bottom=80),
            ],
        ),
        Drillhole(
            hole_id="H2", x=200, y=200, collar_elev=120, total_depth=80,
            intervals=[
                DrillholeInterval(layer_code="A", depth_top=0, depth_bottom=25, grade=0.8),
                DrillholeInterval(layer_code="B", depth_top=25, depth_bottom=60, grade=1.6),
                DrillholeInterval(layer_code="C", depth_top=60, depth_bottom=80),
            ],
        ),
    ]


def _fixed_mesh():
    holes = _fixed_holes()
    spec = ModelingSpec(
        bounds=Bounds(min=[0, 0, 0], max=[200, 200, 200]),
        stratigraphy=[
            StratigraphyEntry(layer_code="A", order=0),
            StratigraphyEntry(layer_code="B", order=1),
            StratigraphyEntry(layer_code="C", order=2),
        ],
        drillholes=holes,
        resolution=(16, 16, 10),
        interp=InterpConfig(),
        engine="mock",
    )
    return EngineRegistry().resolve(spec).build(spec, _Ctx()), holes


def test_mesh_golden_hash_and_volume():
    mesh, _ = _fixed_mesh()
    assert mesh.n_cells == 2560
    assert mesh.n_points == 3179
    assert mesh.content_hash() == GOLDEN_HASH
    assert mesh.total_volume() == pytest.approx(GOLDEN_VOLUME, rel=1e-9)


def test_reserve_golden():
    mesh, holes = _fixed_mesh()
    metrics = ReserveMetrics(
        body_layers=["B"], method="volume", density=2.6, cutoff_grade=0.5,
        min_thickness=1.0, grade_unit="%", measured_distance=150, indicated_distance=300,
    )
    result = estimate_reserves(mesh, metrics, drillholes=holes, interp=InterpConfig())
    assert result.total_tonnage == pytest.approx(GOLDEN_TONNAGE, rel=1e-6)
    assert result.total_metal == pytest.approx(GOLDEN_METAL, rel=1e-6)


def test_section_golden_bounds():
    mesh, _ = _fixed_mesh()
    plane = make_horizontal_section(100, bounds=[[0, 0, 0], [200, 200, 200]])
    result = build_section(mesh, plane)
    assert result.bounds_uv == pytest.approx([0, 0, 200, 200])
    assert set(result.polylines) == {"A"}
