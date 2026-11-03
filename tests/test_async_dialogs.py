"""储量/剖面/统计对话框后台计算测试。"""
from __future__ import annotations

from geomodel3d.app.context import AppContext
from geomodel3d.config.schema import AppSettings, Bounds, StratigraphyEntry
from geomodel3d.services.modeling_service import spec_from_project
from geomodel3d.ui.dialogs.reserve_dialog import ReserveDialog
from geomodel3d.ui.dialogs.section_dialog import SectionDialog
from geomodel3d.ui.dialogs.stats_dialog import StatsDialog

CSV = (
    "孔号,东坐标,北坐标,孔口高程,层顶深,层底深,地层代码,品位\n"
    "H1,0,0,100,0,20,A,1.2\nH1,0,0,100,20,50,B,2.4\n"
    "H2,180,180,120,0,25,A,0.9\nH2,180,180,120,25,60,B,1.6\n"
)


def _context(tmp_path):
    context = AppContext(
        AppSettings(theme="light"), log_dir=tmp_path / "logs", enable_tasks=True
    )
    project = context.projects.new_project(
        "demo", bounds=Bounds(min=[0, 0, 0], max=[200, 200, 200])
    )
    project.config.stratigraphy = [
        StratigraphyEntry(layer_code="A", order=0),
        StratigraphyEntry(layer_code="B", order=1),
    ]
    csv = tmp_path / "bh.csv"
    csv.write_text(CSV, encoding="utf-8-sig")
    context.data.import_boreholes(csv)
    context.modeling.create_run(
        spec_from_project(project, engine="mock", resolution=(8, 8, 6)),
        async_run=False,
    )
    return context


def _shutdown(context):
    if context.tasks is not None:
        context.tasks.shutdown()


def _wait_until(qapp, predicate, timeout=5.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return bool(predicate())


def test_reserve_dialog_runs_in_background(qapp, tmp_path):
    context = _context(tmp_path)
    dialog = ReserveDialog(context)
    try:
        dialog._run()
        assert dialog._result is None
        assert _wait_until(qapp, lambda: dialog._result is not None)
        assert dialog._table.rowCount() >= 1
        assert dialog._export_btn.isEnabled()
    finally:
        dialog.close()
        _shutdown(context)


def test_section_dialog_runs_in_background(qapp, tmp_path):
    context = _context(tmp_path)
    dialog = SectionDialog(context)
    try:
        dialog._run()
        assert dialog.results == []
        assert _wait_until(qapp, lambda: bool(dialog.results))
        assert dialog._export_btn.isEnabled()
        assert dialog.results[0].polylines
    finally:
        dialog.close()
        _shutdown(context)


def test_stats_dialog_runs_in_background(qapp, tmp_path):
    context = _context(tmp_path)
    dialog = StatsDialog(context)
    try:
        assert _wait_until(qapp, lambda: bool(dialog._payload))
        assert "thickness" in dialog._payload
        assert "grade" in dialog._payload
    finally:
        dialog.close()
        _shutdown(context)
