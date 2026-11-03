"""服务层端到端测试（headless）。"""
from __future__ import annotations

import pytest

from geomodel3d.app.context import AppContext
from geomodel3d.config.schema import AppSettings, Bounds, StratigraphyEntry
from geomodel3d.domain.entities import Fault, FaultParams, FaultSide
from geomodel3d.services.modeling_service import spec_from_project

CSV = (
    "孔号,东坐标,北坐标,孔口高程,层顶深,层底深,地层代码,品位\n"
    "H1,0,0,100,0,20,A,1.2\n"
    "H1,0,0,100,20,50,B,0.4\n"
    "H2,180,180,120,0,25,A,0.9\n"
    "H2,180,180,120,25,60,B,0.3\n"
)


@pytest.fixture()
def app_context(tmp_path):
    settings = AppSettings(theme="light", default_crs_epsg=32649)
    return AppContext(settings, log_dir=tmp_path / "logs", enable_tasks=False)


def _seed(context, tmp_path):
    project = context.projects.new_project("demo", bounds=Bounds(min=[0, 0, 0], max=[200, 200, 200]))
    project.config.stratigraphy = [
        StratigraphyEntry(layer_code="A", order=0),
        StratigraphyEntry(layer_code="B", order=1),
    ]
    csv = tmp_path / "bh.csv"
    csv.write_text(CSV, encoding="utf-8-sig")
    context.data.import_boreholes(csv)
    project.config.engine_policy = "mock"
    project.faults = [
        Fault(
            fault_id="F1",
            trace=[[0, 100, 0], [200, 100, 0]],
            params=FaultParams(length=200, depth_of_influence=200, dip=60, strike=90, throw_v=10),
            side=FaultSide.NORMAL,
        )
    ]
    return project


def test_new_project_and_import(app_context, tmp_path):
    project = _seed(app_context, tmp_path)
    assert project.name == "demo"
    assert len(project.drillholes) == 2
    assert app_context.data.get_quality_report().passed


def test_import_derives_stratigraphy(tmp_path):
    context = AppContext(AppSettings(), log_dir=tmp_path / "logs", enable_tasks=False)
    project = context.projects.new_project("demo", bounds=Bounds(min=[0, 0, 0], max=[200, 200, 200]))
    assert not project.config.stratigraphy
    csv = tmp_path / "bh.csv"
    csv.write_text(CSV, encoding="utf-8-sig")
    context.data.import_boreholes(csv)
    codes = [entry.layer_code for entry in sorted(project.config.stratigraphy, key=lambda e: e.order)]
    assert codes == ["A", "B"]
    context.modeling.create_run(
        spec_from_project(project, engine="mock", resolution=(6, 6, 4)), async_run=False
    )
    assert project.has_model()


def test_import_autofills_crs(tmp_path):
    context = AppContext(AppSettings(), log_dir=tmp_path / "logs", enable_tasks=False)
    project = context.projects.new_project(
        "geo", bounds=Bounds(min=[116.0, 39.0, 0], max=[117.0, 40.0, 100])
    )
    assert project.crs.epsg is None
    csv = tmp_path / "bh.csv"
    csv.write_text(
        "孔号,东坐标,北坐标,孔口高程,层顶深,层底深,地层代码\n"
        "H1,116.3,39.9,50,0,20,A\nH2,116.4,39.8,55,0,22,A\n",
        encoding="utf-8-sig",
    )
    context.data.import_boreholes(csv)
    # 经纬度坐标应自动识别为 CGCS2000 地理坐标
    assert project.crs.epsg in (4490, 4326)
    assert project.config.project.source_crs.epsg in (4490, 4326)


def test_create_fault(app_context, tmp_path):
    project = _seed(app_context, tmp_path)
    before = len(project.faults)
    fault = app_context.fault_editor.create_fault()
    assert len(project.faults) == before + 1
    assert len(fault.trace) == 2
    region = app_context.fault_editor.preview_impact(fault.fault_id)
    assert region[0] >= 0 and region[3] <= 200


def test_engine_fallback_on_unavailable(app_context, tmp_path):
    from geomodel3d.config.errors import EngineUnavailableError

    project = _seed(app_context, tmp_path)

    class _Failing:
        name = "failing"

        def build(self, spec, ctx):
            raise EngineUnavailableError("engine down")

    app_context.modeling._registry._engines["failing"] = _Failing()
    spec = spec_from_project(project, engine="failing", resolution=(6, 6, 4))
    meta = app_context.modeling.create_run(spec, async_run=False, engine_name="failing")
    assert meta.status.value == "finished"
    assert meta.engine.value == "mock"  # 回退后运行记录应反映真实引擎
    assert project.has_model()


def test_unify_crs_idempotent(app_context, tmp_path):
    from geomodel3d.config.schema import CRSConfig

    project = _seed(app_context, tmp_path)
    project.config.project.source_crs = CRSConfig(epsg=32650)
    project.config.project.crs = CRSConfig(epsg=32649)
    original = (project.drillholes[0].x, project.drillholes[0].y)

    app_context.data.unify_crs()
    first = (project.drillholes[0].x, project.drillholes[0].y)
    # 必须真的发生变换，而非原样通过
    assert first != original
    assert project.config.project.source_crs.epsg == 32649  # 变换后源=目标

    app_context.data.unify_crs()  # 第二次不应再次变换
    assert (project.drillholes[0].x, project.drillholes[0].y) == first
    # 断层/地震坐标也应参与变换
    assert project.faults[0].trace[0][0] != 0.0


def test_sync_modeling(app_context, tmp_path):
    project = _seed(app_context, tmp_path)
    spec = spec_from_project(project, resolution=(8, 8, 6))
    meta = app_context.modeling.create_run(spec, async_run=False)
    assert meta.status.value == "finished"
    assert project.has_model()
    assert project.mesh.n_cells == 8 * 8 * 6


def test_fault_edit_and_undo(app_context, tmp_path):
    project = _seed(app_context, tmp_path)
    spec = spec_from_project(project, resolution=(8, 8, 6))
    app_context.modeling.create_run(spec, async_run=False)

    region = app_context.fault_editor.preview_impact("F1")
    assert region[0] >= 0 and region[3] <= 200

    app_context.fault_editor.update_fault(
        "F1",
        FaultParams(
            length=200, depth_of_influence=200, dip=45, strike=90,
            throw_v=20, throw_h=50,
        ),
        commit=True,
        async_run=False,
        resolution=(8, 8, 6),
    )
    assert project.faults[0].params.dip == 45
    assert project.faults[0].params.throw_h == 50
    assert project.can_undo()

    assert app_context.fault_editor.undo()
    assert project.faults[0].params.dip == 60
    assert project.faults[0].params.throw_h == 0

    assert app_context.fault_editor.redo()
    assert project.faults[0].params.dip == 45
    assert project.faults[0].params.throw_h == 50


def test_model_changed_event(app_context, tmp_path):
    project = _seed(app_context, tmp_path)
    events = []
    from geomodel3d.app.events import MODEL_CHANGED

    app_context.bus.subscribe(MODEL_CHANGED, lambda e: events.append(e.payload))
    spec = spec_from_project(project, resolution=(6, 6, 4))
    app_context.modeling.create_run(spec, async_run=False)
    assert events and "version" in events[-1]


def test_autofit_bounds_on_out_of_range_boreholes(tmp_path):
    from geomodel3d.config.schema import AppSettings

    context = AppContext(AppSettings(), log_dir=tmp_path / "logs", enable_tasks=False)
    project = context.projects.new_project("utm")  # 默认包围盒 0~1000
    csv = tmp_path / "utm.csv"
    csv.write_text(
        "孔号,东坐标,北坐标,孔口高程,层顶深,层底深,地层代码\n"
        "H1,499000,3200000,100,0,20,A\nH2,499100,3200100,110,0,22,A\n",
        encoding="utf-8-sig",
    )
    context.data.import_boreholes(csv)
    bounds = project.config.project.bounds
    assert bounds.min[0] < 499000 < bounds.max[0]
    assert bounds.min[1] < 3200000 < bounds.max[1]
    report = context.data.get_quality_report()
    assert any(issue.code == "DAT-BND-AUTO" for issue in report.issues)


def test_autofit_bounds_covers_positive_elevations(tmp_path, caplog):
    """局部坐标 + 正高程时，默认 z 范围 [-1000,0] 也必须被自动重设，否则退化为单层方块。"""
    from geomodel3d.config.schema import AppSettings

    context = AppContext(AppSettings(default_crs_epsg=None), log_dir=tmp_path / "logs", enable_tasks=False)
    project = context.projects.new_project("local")
    csv = tmp_path / "local.csv"
    csv.write_text(
        "孔号,东坐标,北坐标,孔口高程,层顶深,层底深,地层代码\n"
        "H1,0,0,100,0,20,A\nH1,0,0,100,20,50,B\n"
        "H2,180,180,120,0,25,A\nH2,180,180,120,25,60,B\n",
        encoding="utf-8-sig",
    )
    context.data.import_boreholes(csv)
    bounds = project.config.project.bounds
    assert bounds.min[2] < 100 and bounds.max[2] > 120

    spec = spec_from_project(project, engine="mock", resolution=(8, 8, 6))
    context.modeling.create_run(spec, async_run=False)
    codes = set(project.mesh.cell_layer_codes().tolist())
    assert codes == {"A", "B"}  # 不能退化为单层


def test_unify_crs_per_seismic_source(app_context, tmp_path):
    from geomodel3d.config.schema import CRSConfig
    from geomodel3d.domain.entities import SeismicLayer

    project = _seed(app_context, tmp_path)
    project.config.project.source_crs = CRSConfig(epsg=32650)
    project.config.project.crs = CRSConfig(epsg=32650)
    before = (project.drillholes[0].x, project.drillholes[0].y)
    project.seismic.append(
        SeismicLayer(
            name="s",
            points=[[116.3, 39.9, 10.0], [116.4, 39.9, 11.0]],
            source_crs_epsg=4326,
        )
    )
    app_context.data.unify_crs()
    # 钻孔源=目标，不应被二次变换
    assert (project.drillholes[0].x, project.drillholes[0].y) == before
    # 地震按自带源 CRS(4326) 变换到 32650，x 变为带号量级
    assert project.seismic[0].source_crs_epsg == 32650
    assert project.seismic[0].points[0][0] > 1000


def test_snapshot_created_before_run(app_context, tmp_path):
    project = _seed(app_context, tmp_path)
    spec = spec_from_project(project, engine="mock", resolution=(6, 6, 4))
    app_context.modeling.create_run(spec, async_run=False)
    count_before = len(project.snapshots)
    spec2 = spec_from_project(project, engine="mock", resolution=(6, 6, 4))
    app_context.modeling.create_run(spec2, async_run=False)
    assert len(project.snapshots) == count_before + 1
    assert project.snapshots[-1].kind == "model"


def test_sync_failure_flags_caller_handled(app_context, tmp_path):
    from geomodel3d.app.events import RUN_FAILED

    project = _seed(app_context, tmp_path)
    spec = spec_from_project(project, engine="mock", resolution=(6, 6, 4))
    spec.interp = spec.interp.model_copy(update={"method": "gempy"})
    payloads = []
    app_context.bus.subscribe(RUN_FAILED, lambda e: payloads.append(e.payload))
    with pytest.raises(Exception):
        app_context.modeling.create_run(spec, async_run=False)
    assert payloads and payloads[-1]["handled_by_caller"] is True


def test_repair_service_sync_fallback(app_context, tmp_path):
    project = _seed(app_context, tmp_path)
    spec = spec_from_project(project, engine="mock", resolution=(6, 6, 4))
    app_context.modeling.create_run(spec, async_run=False)
    # �����������ʱ async_run=True ҲӦͬ��ִ�в����ر���
    report = app_context.repair.repair(async_run=True)
    assert hasattr(report, "summary")
    assert project.has_model()


def test_fault_cut_service(app_context, tmp_path):
    pytest.importorskip("pyvista")
    project = _seed(app_context, tmp_path)
    spec = spec_from_project(project, engine="mock", resolution=(8, 8, 6))
    app_context.modeling.create_run(spec, async_run=False)
    before = len(project.snapshots)

    mesh = app_context.fault_editor.cut_fault("F1")
    assert mesh.meta["engine"] == "vtk_fault_cut"
    assert mesh.meta["fault_cut"]["watertight"] is True
    assert project.mesh is mesh
    assert len(project.snapshots) == before + 1


def test_encrypted_project_service_roundtrip(app_context, tmp_path):
    pytest.importorskip("cryptography")
    from geomodel3d.dataio.encryption import is_encrypted

    project = _seed(app_context, tmp_path)
    app_context.projects.password = "secret"
    path = app_context.projects.save(tmp_path / "secret.gm3d")
    assert is_encrypted(path)

    app_context.projects.close()
    restored = app_context.projects.open_project(path, password="secret")
    assert restored.name == project.name
    assert app_context.projects.password == "secret"


def test_encrypted_autosave_recovery(app_context, tmp_path):
    pytest.importorskip("cryptography")
    from geomodel3d.dataio.encryption import is_encrypted

    project = _seed(app_context, tmp_path)
    app_context.projects.password = "secret"
    path = app_context.projects.save(tmp_path / "secret.gm3d")

    project.config.project.name = "edited"
    project.mark_dirty()
    autosave = app_context.projects.autosave()
    assert autosave is not None and is_encrypted(autosave)

    recovered = app_context.projects.recover()
    assert recovered.name == "edited"
    assert recovered.path == path
