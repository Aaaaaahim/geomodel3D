"""储量计算算法测试（M4 / 6.4）。"""
from __future__ import annotations

import numpy as np
import pytest

from geomodel3d.algorithms.reserve import estimate_reserves
from geomodel3d.domain.reserve import ReserveMetrics
from geomodel3d.engine import EngineRegistry
from geomodel3d.engine.umesh import VTK_HEXAHEDRON, UMesh


@pytest.fixture()
def ore_mesh(project, modeling_spec, ctx):
    return EngineRegistry().resolve(modeling_spec).build(modeling_spec, ctx)


def _metrics(**overrides):
    base = dict(
        mineral="coal",
        body_layers=["B"],
        method="volume",
        density=2.6,
        cutoff_grade=0.5,
        min_thickness=1.0,
        grade_unit="%",
        measured_distance=150.0,
        indicated_distance=300.0,
    )
    base.update(overrides)
    return ReserveMetrics(**base)


def test_volume_method_totals(ore_mesh, project):
    result = estimate_reserves(ore_mesh, _metrics(), drillholes=project.drillholes)
    assert result.total_volume > 0
    assert result.total_tonnage == pytest.approx(result.total_volume * 2.6, rel=1e-6)
    assert result.total_metal > 0
    assert 1.0 < result.avg_grade < 3.0


def test_block_method_same_volume(ore_mesh, project):
    volume_result = estimate_reserves(ore_mesh, _metrics(method="volume"), drillholes=project.drillholes)
    block_result = estimate_reserves(
        ore_mesh, _metrics(method="block", block_size=[50, 50, 50]), drillholes=project.drillholes
    )
    assert block_result.total_volume == pytest.approx(volume_result.total_volume, rel=1e-6)
    assert len(block_result.blocks) > len(volume_result.blocks)


def test_classification(ore_mesh, project):
    result = estimate_reserves(ore_mesh, _metrics(), drillholes=project.drillholes)
    assert result.by_class
    assert all(key in {"331", "332", "333"} for key in result.by_class)


def test_no_body_warns(ore_mesh):
    result = estimate_reserves(ore_mesh, _metrics(body_layers=["NOPE"]))
    assert result.total_volume == 0
    assert result.warnings


def test_cutoff_filters(ore_mesh, project):
    low = estimate_reserves(ore_mesh, _metrics(cutoff_grade=0.0), drillholes=project.drillholes)
    high = estimate_reserves(ore_mesh, _metrics(cutoff_grade=100.0), drillholes=project.drillholes)
    assert low.total_volume > 0
    assert high.total_volume == 0  # 边界品位 100 应筛掉全部单元


def _column_mesh(grades):
    """构造单柱多层模型（每层 10m，面组归属矿体地层 B）。"""
    from geomodel3d.engine.layered import _voxel_grid

    count = len(grades)
    xs = np.array([0.0, 10.0])
    ys = np.array([0.0, 10.0])
    zs = np.arange(count + 1, dtype=float) * 10.0
    vertices, cells = _voxel_grid(xs, ys, zs)
    return UMesh(
        vertices=vertices,
        cells=cells,
        cell_types=np.full(count, VTK_HEXAHEDRON, dtype=np.int32),
        cell_attr={
            "layer": np.zeros(count, dtype=np.int32),
            "grade": np.asarray(grades, dtype=np.float64),
        },
        face_groups={"B": list(range(count))},
    )


def test_waste_interbed_thick_removed():
    mesh = _column_mesh([2.0, 0.0, 2.0])  # 中间 10m 夹石（品位低于边界）
    result = estimate_reserves(
        mesh, _metrics(cutoff_grade=1.0, min_thickness=0.0, max_waste_thickness=5.0)
    )
    assert result.total_volume == pytest.approx(2000.0)  # 夹石 >= 阈值：剔除


def test_waste_interbed_thin_diluted():
    mesh = _column_mesh([2.0, 0.0, 2.0])
    result = estimate_reserves(
        mesh, _metrics(cutoff_grade=1.0, min_thickness=0.0, max_waste_thickness=20.0)
    )
    assert result.total_volume == pytest.approx(3000.0)  # 夹石 < 阈值：并入矿体


def test_waste_removal_disabled_by_default():
    mesh = _column_mesh([2.0, 0.0, 2.0])
    result = estimate_reserves(
        mesh, _metrics(cutoff_grade=1.0, min_thickness=0.0, max_waste_thickness=0.0)
    )
    assert result.total_volume == pytest.approx(2000.0)  # 默认关闭夹石剔除
