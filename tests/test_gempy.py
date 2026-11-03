"""GemPy 适配器测试（转换逻辑可离线验证；真实引擎按可用性跳过）。"""
from __future__ import annotations

import logging

import numpy as np
import pytest

from geomodel3d.config.errors import EngineUnavailableError
from geomodel3d.config.schema import Bounds, InterpConfig, StratigraphyEntry
from geomodel3d.domain.entities import Drillhole, DrillholeInterval
from geomodel3d.engine import ModelingSpec
from geomodel3d.engine.gempy_adapter import (
    GemPyEngine,
    _build_constraints,
    _lith_block_to_layer_index,
    _lith_to_mesh,
    gempy_available,
)


class _Ctx:
    def progress(self, percent: float, stage: str = "") -> None:
        return None

    def raise_if_cancelled(self) -> None:
        return None


def _layers():
    return [
        StratigraphyEntry(layer_code="A", order=0),
        StratigraphyEntry(layer_code="B", order=1),
        StratigraphyEntry(layer_code="C", order=2),
    ]


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


def test_build_constraints_interfaces():
    surface_points, orientations = _build_constraints(_layers(), _holes())
    # 两个界面（A/B 与 B/C），每界面 2 个孔 -> 4 个界面点
    assert len(surface_points) == 4
    assert set(surface_points["surface"]) == {"A", "B"}
    # A/B 界面：H1 顶面 100-20=80，H2 顶面 120-25=95
    a_points = surface_points[surface_points["surface"] == "A"]["Z"].tolist()
    assert sorted(a_points) == [80.0, 95.0]
    assert len(orientations) == 2


def test_lith_block_to_layer_index():
    # 3 层，分辨率 2x1x1 -> 层号 1,2 应转为 0,1
    block = np.array([1, 2])
    index = _lith_block_to_layer_index(block, (2, 1, 1))
    assert index.tolist() == [0, 1]


def test_lith_to_mesh():
    spec = ModelingSpec(
        bounds=Bounds(min=[0, 0, 0], max=[200, 200, 200]),
        stratigraphy=_layers(),
        drillholes=_holes(),
        resolution=(2, 2, 2),
        interp=InterpConfig(),
    )
    block = np.array([1, 1, 1, 1, 2, 2, 2, 2])
    mesh = _lith_to_mesh(block, spec)
    assert mesh.n_cells == 8
    assert mesh.meta["engine"] == "gempy"
    assert set(mesh.cell_attr["layer"].tolist()) == {0, 1}


def test_lith_to_mesh_falls_back_when_surface_meta_fails(monkeypatch, caplog):
    """层界面元数据生成失败时应记录告警并回退，而不是抛 NameError。"""
    import geomodel3d.engine.gempy_adapter as adapter

    spec = ModelingSpec(
        bounds=Bounds(min=[0, 0, 0], max=[200, 200, 200]),
        stratigraphy=_layers(),
        drillholes=_holes(),
        resolution=(2, 2, 2),
        interp=InterpConfig(),
    )
    block = np.array([1, 1, 1, 1, 2, 2, 2, 2])

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic surface metadata failure")

    monkeypatch.setattr(adapter, "_layer_boundaries", _boom)
    with caplog.at_level(logging.WARNING, logger="geomodel3d.engine.gempy_adapter"):
        mesh = _lith_to_mesh(block, spec)

    assert mesh.n_cells == 8
    assert mesh.meta["layer_surfaces"] is None
    assert "回退等值面显示" in caplog.text


def test_engine_unavailable_raises():
    if gempy_available():
        pytest.skip("GemPy 可用，真实构建路径由 test_gempy_bridge 覆盖")
    spec = ModelingSpec(
        bounds=Bounds(min=[0, 0, 0], max=[10, 10, 10]),
        stratigraphy=_layers(),
        resolution=(2, 2, 2),
    )
    with pytest.raises(EngineUnavailableError):
        GemPyEngine().build(spec, _Ctx())
