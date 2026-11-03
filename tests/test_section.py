"""剖面生成算法测试（M5 / 6.5）。"""
from __future__ import annotations

import numpy as np
import pytest

from geomodel3d.algorithms.section import (
    build_section,
    make_arbitrary_section,
    make_exploration_sections,
    make_horizontal_section,
)
from geomodel3d.engine import EngineRegistry


@pytest.fixture()
def section_mesh(project, modeling_spec, ctx):
    return EngineRegistry().resolve(modeling_spec).build(modeling_spec, ctx)


def test_arbitrary_section_loops(section_mesh):
    plan = make_arbitrary_section([0, 100], [200, 100], bounds=[[0, 0, 0], [200, 200, 200]])
    result = build_section(section_mesh, plan)
    assert result.polylines
    for polylines in result.polylines.values():
        assert len(polylines) >= 1
        assert all(len(line) >= 4 for line in polylines)
    assert result.bounds_uv[0] == pytest.approx(0.0, abs=1e-6)
    assert result.bounds_uv[2] == pytest.approx(200.0, abs=1e-6)
    assert result.bounds_uv[1] == pytest.approx(0.0, abs=1e-6)
    assert result.bounds_uv[3] == pytest.approx(200.0, abs=1e-6)


def test_world_coordinates_on_plane(section_mesh):
    plan = make_arbitrary_section([0, 100], [200, 100], bounds=[[0, 0, 0], [200, 200, 200]])
    result = build_section(section_mesh, plan)
    for world in result.world_polylines.values():
        for line in world:
            for _, y, _ in line:
                assert y == pytest.approx(100.0, abs=1e-6)


def test_horizontal_section(section_mesh):
    plan = make_horizontal_section(50, bounds=[[0, 0, 0], [200, 200, 200]])
    result = build_section(section_mesh, plan)
    assert result.polylines
    assert result.bounds_uv == pytest.approx([0, 0, 200, 200], abs=1e-6)


def test_borehole_projection(section_mesh, project):
    plan = make_arbitrary_section(
        [0, 0], [200, 0], bounds=[[0, 0, 0], [200, 200, 200]], offset_window=50
    )
    result = build_section(section_mesh, plan, drillholes=project.drillholes)
    assert len(result.boreholes) >= 1
    hole = result.boreholes[0]
    assert hole.intervals
    assert all(iv.top_v >= iv.base_v for iv in hole.intervals)


def test_exploration_batch():
    base = make_arbitrary_section([0, 100], [200, 100], bounds=[[0, 0, 0], [200, 200, 200]])
    plans = make_exploration_sections(base, spacing=50, count=3)
    assert len(plans) == 3
    origins = np.array([plan.origin for plan in plans])
    assert np.ptp(origins[:, 1]) == pytest.approx(100.0)


def test_fault_trace_with_projected_coordinates(ctx):
    """投影坐标（UTM）下断层面与剖面交线不应因锚点远离原点而丢失。"""
    from geomodel3d.config.schema import Bounds, InterpConfig, StratigraphyEntry
    from geomodel3d.engine import ModelingSpec
    from geomodel3d.engine.mock import MockEngine
    from geomodel3d.domain.entities import Fault, FaultParams

    spec = ModelingSpec(
        bounds=Bounds(min=[499000, 3459800, 0], max=[500700, 3461400, 200]),
        stratigraphy=[
            StratigraphyEntry(layer_code="A", order=0),
            StratigraphyEntry(layer_code="B", order=1),
            StratigraphyEntry(layer_code="C", order=2),
        ],
        resolution=(10, 10, 6),
        interp=InterpConfig(),
    )
    mesh = MockEngine().build(spec, ctx)
    fault = Fault(
        fault_id="F1",
        trace=[[499000, 3460600, 0], [500700, 3460600, 0]],
        params=FaultParams(length=1700, depth_of_influence=200, dip=65, strike=90, throw_v=20),
    )
    plan = make_arbitrary_section(
        [499850, 3459800], [499850, 3461400],
        bounds=[[499000, 3459800, 0], [500700, 3461400, 200]],
    )
    result = build_section(mesh, plan, faults=[fault])
    assert "F1" in result.fault_traces
    assert len(result.fault_traces["F1"]) >= 2


def test_section_azimuth():
    from geomodel3d.algorithms.section import section_azimuth

    east = make_arbitrary_section([0, 0], [200, 0], bounds=[[0, 0, 0], [200, 200, 200]])
    north = make_arbitrary_section([0, 0], [0, 200], bounds=[[0, 0, 0], [200, 200, 200]])
    assert section_azimuth(east) == pytest.approx(90.0)
    assert section_azimuth(north) == pytest.approx(0.0)


def test_section_annotations(section_mesh, project):
    plan = make_arbitrary_section(
        [0, 100], [200, 100], bounds=[[0, 0, 0], [200, 200, 200]], offset_window=300
    )
    result = build_section(section_mesh, plan, drillholes=project.drillholes)
    kinds = {item["kind"] for item in result.annotations()}
    assert "layer" in kinds
    if result.boreholes:
        assert "thickness" in kinds


def test_fault_trace_in_section(project, modeling_spec, ctx):
    from geomodel3d.domain.entities import Fault, FaultParams
    from geomodel3d.engine import EngineRegistry

    fault = Fault(
        fault_id="F1",
        trace=[[0, 100, 0], [200, 100, 0]],
        params=FaultParams(length=200, depth_of_influence=200, dip=60, strike=90, throw_v=20),
    )
    modeling_spec.faults = [fault]
    mesh = EngineRegistry().resolve(modeling_spec).build(modeling_spec, ctx)
    plan = make_arbitrary_section([0, 0], [200, 0], bounds=[[0, 0, 0], [200, 200, 200]])
    result = build_section(mesh, plan, faults=[fault])
    assert "F1" in result.fault_traces
    assert len(result.fault_traces["F1"]) >= 2
    assert result.fault_labels and result.fault_labels[0]["fault_id"] == "F1"
