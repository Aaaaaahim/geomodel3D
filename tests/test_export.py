"""导出与网格体检测试（M8 / BR-09）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from geomodel3d.algorithms.mesh_health import HealthSeverity, health_check
from geomodel3d.app.context import AppContext
from geomodel3d.config.errors import GeoModelError
from geomodel3d.config.schema import AppSettings, Bounds, CRSConfig, StratigraphyEntry
from geomodel3d.dataio.exporters import export_section
from geomodel3d.domain.section import SectionPlan
from geomodel3d.engine import EngineRegistry
from geomodel3d.services.modeling_service import spec_from_project

CSV = (
    "孔号,东坐标,北坐标,孔口高程,层顶深,层底深,地层代码,品位\n"
    "H1,0,0,100,0,20,A,1.2\n"
    "H1,0,0,100,20,50,B,2.4\n"
    "H2,180,180,120,0,25,A,0.9\n"
    "H2,180,180,120,25,60,B,1.6\n"
)


@pytest.fixture()
def context(tmp_path):
    settings = AppSettings(default_crs_epsg=32649)
    ctx = AppContext(settings, log_dir=tmp_path / "logs", enable_tasks=False)
    project = ctx.projects.new_project("demo", bounds=Bounds(min=[0, 0, 0], max=[200, 200, 200]))
    project.config.stratigraphy = [
        StratigraphyEntry(layer_code="A", order=0),
        StratigraphyEntry(layer_code="B", order=1),
    ]
    csv = tmp_path / "bh.csv"
    csv.write_text(CSV, encoding="utf-8-sig")
    ctx.data.import_boreholes(csv)
    spec = spec_from_project(project, engine="mock", resolution=(8, 8, 6))
    ctx.modeling.create_run(spec, async_run=False)
    return ctx


def test_health_check_ok(context):
    report = health_check(context.projects.project.mesh)
    assert report.metrics["cells"] > 0
    assert not any(i.severity == HealthSeverity.ERROR for i in report.issues)


def test_export_mesh_vtu(context, tmp_path):
    path = context.exporter.export_mesh(tmp_path / "model.vtu")
    assert path.exists() and path.stat().st_size > 0


def test_export_mesh_obj(context, tmp_path):
    path = context.exporter.export_mesh(tmp_path / "model.obj")
    assert path.exists() and path.stat().st_size > 0


def test_section_exports(context, tmp_path):
    result = context.section.create_horizontal(60)
    for fmt in ("geojson", "dxf", "svg", "png", "shp"):
        path = export_section(result, tmp_path / f"section.{fmt}", fmt)
        assert path.exists() and path.stat().st_size > 0, fmt


def test_vector_export_requires_crs(context, tmp_path):
    project = context.projects.project
    project.config.project.crs = CRSConfig()
    result = context.section.create_horizontal(60)
    result.plan.crs_epsg = None
    with pytest.raises(GeoModelError):
        context.exporter.export_section(result, tmp_path / "s.shp", "shp")


def test_checklist_blocks_without_crs(context):
    project = context.projects.project
    project.config.project.crs = CRSConfig()
    checklist = context.exporter.checklist(fmt="shp")
    assert not checklist.passed


def test_dxf_layer_naming_ore_vs_geol(tmp_path):
    import ezdxf

    from geomodel3d.domain.section import SectionPlan, SectionResult

    result = SectionResult(
        plan=SectionPlan(name="s", crs_epsg=32649),
        world_polylines={
            "A": [[[0, 0, 0], [10, 0, 0]]],
            "B": [[[0, 5, 0], [10, 5, 0]]],
        },
    )
    result.ore_layers = ["B"]
    path = export_section(result, tmp_path / "s.dxf", "dxf")
    doc = ezdxf.readfile(path)
    names = {layer.dxf.name for layer in doc.layers}
    assert "GEOL_A" in names
    assert "ORE_B" in names
    assert "ORE_A" not in names
    assert "GEOL_B" not in names
