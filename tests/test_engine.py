"""引擎调度与 Mock 引擎测试。"""
from __future__ import annotations

import numpy as np
import pytest

from geomodel3d.config.errors import EngineUnavailableError, GeoModelError
from geomodel3d.domain.entities import EngineKind, Fault, FaultParams, FaultSide
from geomodel3d.engine import EngineContext, EngineRegistry, ModelingSpec, decide_engine
from geomodel3d.engine.gempy_adapter import GemPyEngine
from geomodel3d.engine.opengeode_engine import OpenGeodeEngine


def test_decide_engine_manual_wins():
    ctx = EngineContext(manual_engine="gempy", has_faults=True)
    assert decide_engine(ctx) == EngineKind.GEMPY


def test_decide_engine_faults_high_precision():
    assert decide_engine(EngineContext(has_faults=True)) == EngineKind.OPENGEODE
    assert decide_engine(EngineContext(need_high_precision=True)) == EngineKind.OPENGEODE
    assert decide_engine(EngineContext(target="reserve")) == EngineKind.OPENGEODE


def test_decide_engine_quick_preview():
    assert decide_engine(EngineContext(purpose="quick_preview")) == EngineKind.GEMPY


def test_registry_explicit_mock(modeling_spec):
    registry = EngineRegistry()
    engine = registry.resolve(modeling_spec)
    assert engine.name == "mock"


def test_mock_engine_build(modeling_spec, ctx):
    mesh = EngineRegistry().resolve(modeling_spec).build(modeling_spec, ctx)
    assert mesh.n_cells == 8 * 8 * 6
    assert mesh.total_volume() == pytest.approx(200 * 200 * 200)
    assert mesh.face_groups
    assert mesh.meta["engine"] == "mock"


def test_mock_engine_fault_offset(modeling_spec, ctx):
    modeling_spec.faults = [
        Fault(
            fault_id="F1",
            trace=[[0, 100, 0], [200, 100, 0]],
            params=FaultParams(length=200, depth_of_influence=200, dip=60, strike=90, throw_v=40),
        )
    ]
    mesh = EngineRegistry().resolve(modeling_spec).build(modeling_spec, ctx)
    assert mesh.cell_attr["fault_affected"].sum() > 0


def test_fault_horizontal_throw_shifts_query_xy():
    """水平落差应只把正侧查询点沿断层法向平移。"""
    from geomodel3d.engine.layered import _fault_adjusted_xy

    points = np.array([[0, 0], [0, 200], [200, 0], [200, 200]], dtype=float)
    fault = Fault(
        fault_id="F1",
        trace=[[0, 100, 0], [200, 100, 0]],
        params=FaultParams(length=200, dip=60, strike=90, throw_h=80),
    )
    adjusted = _fault_adjusted_xy(points, [fault])
    assert adjusted[0].tolist() == [0.0, 0.0]
    assert adjusted[2].tolist() == [200.0, 0.0]
    assert adjusted[1].tolist() == [0.0, 280.0]
    assert adjusted[3].tolist() == [200.0, 280.0]

    fault.params.throw_h = 0
    assert np.allclose(_fault_adjusted_xy(points, [fault]), points)


def test_horizontal_throw_strike_slip_shifts_along_strike():
    """平移断层的水平落差应沿走向平移。"""
    from geomodel3d.engine.layered import _fault_adjusted_xy

    points = np.array([[0, 0], [0, 200], [200, 0], [200, 200]], dtype=float)
    fault = Fault(
        fault_id="F1",
        trace=[[0, 100, 0], [200, 100, 0]],
        params=FaultParams(length=200, dip=90, strike=90, throw_h=80),
        side=FaultSide.STRIKE_SLIP,
    )
    adjusted = _fault_adjusted_xy(points, [fault])
    assert adjusted[0].tolist() == [0.0, 0.0]
    assert adjusted[1].tolist() == [80.0, 200.0]
    assert adjusted[3].tolist() == [280.0, 200.0]


def test_horizontal_throw_reverse_shifts_opposite_normal():
    """逆断层的水平落差应与正断层方向相反。"""
    from geomodel3d.engine.layered import _fault_adjusted_xy

    points = np.array([[0, 0], [0, 200], [200, 0], [200, 200]], dtype=float)
    fault = Fault(
        fault_id="F1",
        trace=[[0, 100, 0], [200, 100, 0]],
        params=FaultParams(length=200, dip=60, strike=90, throw_h=80),
        side=FaultSide.REVERSE,
    )
    adjusted = _fault_adjusted_xy(points, [fault])
    assert adjusted[1].tolist() == [0.0, 120.0]
    assert adjusted[3].tolist() == [200.0, 120.0]


def test_mock_engine_horizontal_throw_changes_layers(modeling_spec, ctx):
    """水平落差应改变受影响一侧的地层柱，从而改变分层结果。"""
    modeling_spec.resolution = (16, 16, 10)
    fault = Fault(
        fault_id="F1",
        trace=[[0, 100, 0], [200, 100, 0]],
        params=FaultParams(
            length=200, depth_of_influence=200, dip=60, strike=90,
            throw_v=0, throw_h=150, buffer=40,
        ),
    )
    modeling_spec.faults = [fault]
    shifted = EngineRegistry().resolve(modeling_spec).build(modeling_spec, ctx)
    assert shifted.cell_attr["fault_affected"].sum() > 0

    fault.params.throw_h = 0
    baseline = EngineRegistry().resolve(modeling_spec).build(modeling_spec, ctx)
    assert np.any(
        np.asarray(shifted.cell_attr["layer"]) != np.asarray(baseline.cell_attr["layer"])
    )
    assert shifted.total_volume() == pytest.approx(baseline.total_volume(), rel=1e-9)


def test_unavailable_engine_raises(modeling_spec, ctx):
    """引擎不可用时必须抛 EngineUnavailableError（可用时由 bridge 测试覆盖）。"""
    gem = GemPyEngine()
    if gem.is_available():
        assert gem.describe()["available"] is True
    else:
        with pytest.raises(EngineUnavailableError):
            gem.build(modeling_spec, ctx)

    geode = OpenGeodeEngine()
    if geode.is_available():
        assert geode.describe()["available"] is True
    else:
        with pytest.raises(EngineUnavailableError):
            geode.build(modeling_spec, ctx)


def test_unimplemented_interp_method_fails_loudly(modeling_spec, ctx):
    """选择 gempy 插值必须显式报错，绝不静默降级为 IDW（P1-1）。"""
    modeling_spec.interp = modeling_spec.interp.model_copy(update={"method": "gempy"})
    with pytest.raises(GeoModelError) as excinfo:
        EngineRegistry().resolve(modeling_spec).build(modeling_spec, ctx)
    assert excinfo.value.code.value == "MDL-IPL-002"


def test_mock_engine_dsi_interpolation(modeling_spec, ctx):
    """DSI 近似路径应可完成建模并产出有效分层。"""
    modeling_spec.interp = modeling_spec.interp.model_copy(update={"method": "dsi"})
    mesh = EngineRegistry().resolve(modeling_spec).build(modeling_spec, ctx)
    assert mesh.n_cells == 8 * 8 * 6
    assert mesh.face_groups


def test_bounds_not_covering_data_fails_loudly(modeling_spec, ctx):
    """包围盒 Z 范围未覆盖钻孔界面时必须报错，而非产出单层占位方块（P1-4）。"""
    from geomodel3d.config.schema import Bounds

    modeling_spec.bounds = Bounds(min=[0, 0, -1000], max=[200, 200, -100])
    with pytest.raises(GeoModelError) as excinfo:
        EngineRegistry().resolve(modeling_spec).build(modeling_spec, ctx)
    assert excinfo.value.code.value == "MDL-BND-001"


def test_interface_interpolation_dedupes_xy(monkeypatch, modeling_spec, ctx):
    """界面插值应在 nx*ny 个唯一 XY 列上求值，而非 nx*ny*nz 个单元中心。"""
    import geomodel3d.engine.layered as layered

    seen = []
    original = layered.idw

    def spy(points, values, query, **kwargs):
        seen.append(query.shape[0])
        return original(points, values, query, **kwargs)

    monkeypatch.setattr(layered, "idw", spy)
    modeling_spec.resolution = (8, 8, 6)
    EngineRegistry().resolve(modeling_spec).build(modeling_spec, ctx)
    assert seen, "应至少调用一次插值"
    assert max(seen) == 8 * 8  # 唯一 XY 列数，而非 8*8*6=384


def test_engine_capabilities_fault_cut_honest():
    """OpenGeode/Mock 仅实现位移式断层，不得声明 fault_cut=True（P2-2）。"""
    registry = EngineRegistry()
    for name in ("opengeode", "mock"):
        desc = registry.get(name).describe()
        assert desc["fault_cut"] is False
        assert desc["fault_displacement"] is True
