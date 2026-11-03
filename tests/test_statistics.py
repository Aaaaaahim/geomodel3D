"""统计分析测试（M7）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from geomodel3d.algorithms.statistics import (
    fault_statistics,
    grade_statistics,
    mesh_layer_volumes,
    quality_statistics,
    summarize,
    thickness_statistics,
)
from geomodel3d.app.context import AppContext
from geomodel3d.config.schema import AppSettings, Bounds, StratigraphyEntry
from geomodel3d.domain.entities import Fault, FaultParams
from geomodel3d.domain.rules import QualityReport, Severity
from geomodel3d.engine import EngineRegistry
from geomodel3d.services.modeling_service import spec_from_project

CSV = (
    "孔号,东坐标,北坐标,孔口高程,层顶深,层底深,地层代码,品位\n"
    "H1,0,0,100,0,20,A,1.2\n"
    "H1,0,0,100,20,50,B,2.4\n"
    "H2,180,180,120,0,25,A,0.9\n"
    "H2,180,180,120,25,60,B,1.6\n"
)


def test_summarize():
    stats = summarize([1, 2, 3, 4, 5])
    assert stats["count"] == 5
    assert stats["mean"] == pytest.approx(3.0)
    assert stats["min"] == 1 and stats["max"] == 5


def test_statistics_ignore_nan():
    from geomodel3d.algorithms.statistics import cumulative_curve, histogram

    assert summarize([1.0, float("nan"), 3.0, float("inf")])["count"] == 2
    assert histogram([1.0, float("nan"), 2.0])["counts"]  # 不抛异常
    assert cumulative_curve([float("nan"), 2.0])["percent"] == [100.0]


def test_thickness_statistics(project):
    stats = thickness_statistics(project.drillholes)
    assert set(stats) == {"A", "B", "C"}
    assert stats["A"]["summary"]["count"] == 2
    assert stats["A"]["histogram"]["counts"]


def test_grade_statistics(project):
    stats = grade_statistics(project.drillholes, ["B"])
    assert stats["summary"]["count"] == 2
    assert stats["cumulative"]["percent"][-1] == pytest.approx(100.0)


def test_fault_statistics():
    faults = [
        Fault(fault_id="F1", params=FaultParams(strike=90, dip=60, throw_v=10, length=500, depth_of_influence=200)),
        Fault(fault_id="F2", params=FaultParams(strike=95, dip=45, throw_v=20, length=800, depth_of_influence=300)),
    ]
    stats = fault_statistics(faults)
    assert stats["count"] == 2
    assert sum(stats["rose"]["counts"]) == 2
    assert stats["dip"]["mean"] == pytest.approx(52.5)


def test_quality_statistics():
    report = QualityReport()
    report.add(Severity.ERROR, "E1", "err")
    report.add(Severity.WARNING, "W1", "warn")
    stats = quality_statistics(report)
    assert stats["summary"]["error"] == 1
    assert stats["by_code"]["E1"] == 1


def test_mesh_layer_volumes(project, modeling_spec, ctx):
    mesh = EngineRegistry().resolve(modeling_spec).build(modeling_spec, ctx)
    volumes = mesh_layer_volumes(mesh)
    assert volumes
    assert sum(volumes.values()) == pytest.approx(mesh.total_volume(), rel=1e-6)


@pytest.fixture()
def context(tmp_path):
    ctx = AppContext(AppSettings(default_crs_epsg=32649), log_dir=tmp_path / "logs", enable_tasks=False)
    project = ctx.projects.new_project("demo", bounds=Bounds(min=[0, 0, 0], max=[200, 200, 200]))
    project.config.stratigraphy = [
        StratigraphyEntry(layer_code="A", order=0),
        StratigraphyEntry(layer_code="B", order=1),
    ]
    csv = tmp_path / "bh.csv"
    csv.write_text(CSV, encoding="utf-8-sig")
    ctx.data.import_boreholes(csv)
    ctx.modeling.create_run(spec_from_project(project, engine="mock", resolution=(8, 8, 6)), async_run=False)
    return ctx


def test_stats_collect_and_export(context, tmp_path):
    payload = context.stats.collect()
    assert "thickness" in payload and "grade" in payload and "faults" in payload

    charts = context.stats.export_charts(tmp_path / "charts", fmt="png")
    assert charts and all(Path(p).exists() for p in charts)

    excel = context.stats.export_excel(tmp_path / "stats.xlsx")
    assert Path(excel).exists() and Path(excel).stat().st_size > 0
