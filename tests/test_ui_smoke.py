"""界面冒烟测试（pytest-qt / offscreen）。"""
from __future__ import annotations

import pytest

from geomodel3d.app.context import AppContext
from geomodel3d.config.schema import AppSettings, Bounds
from geomodel3d.domain.entities import Fault, FaultParams
from geomodel3d.ui.main_window import MainWindow


@pytest.fixture()
def window(qapp, tmp_path):
    context = AppContext(
        AppSettings(theme="light"), log_dir=tmp_path / "logs", enable_tasks=False
    )
    win = MainWindow(context)
    yield win
    win.ctx.projects.close()
    win.deleteLater()


def test_main_window_builds(window):
    assert window.tabs.count() == 4
    assert window.tree.topLevelItemCount() == 0
    assert window.act_quick_build.isEnabled() is False


def test_viewport_fullscreen_roundtrip(window, qapp):
    window._new_project()
    assert window.tabs.indexOf(window.viewport) == 0

    window._toggle_viewport_fullscreen()
    assert window._viewport_fullscreen is True
    assert window.tabs.indexOf(window.viewport) == 0
    assert window.viewport.parent() is not None
    assert window.menuBar().isHidden()
    assert window._toolbar.isHidden()
    assert window.left_dock.isHidden()
    assert window.right_dock.isHidden()
    assert window.bottom_dock.isHidden()
    assert window.statusBar().isHidden()
    assert window.tabs.tabBar().isHidden()

    window._fullscreen_shortcut.activated.emit()
    qapp.processEvents()
    assert window._viewport_fullscreen is False
    assert not window.menuBar().isHidden()
    assert not window._toolbar.isHidden()
    assert not window.left_dock.isHidden()
    assert not window.right_dock.isHidden()
    assert not window.bottom_dock.isHidden()
    assert not window.statusBar().isHidden()
    assert not window.tabs.tabBar().isHidden()
    assert window.tabs.currentWidget() is window.viewport


def test_viewport_popout_roundtrip(window, qapp):
    window._new_project()
    window._open_viewport_window()
    assert window._viewport_window is not None
    assert window.tabs.indexOf(window.viewport) == 0

    window._viewport_window.close()
    qapp.processEvents()
    assert window._viewport_window is None
    assert window.tabs.indexOf(window.viewport) == 0


def test_new_project_refreshes_tree(window):
    window._new_project()
    assert window.tree.topLevelItemCount() == 1
    assert window.act_quick_build.isEnabled()
    assert "untitled" in window.windowTitle()


def test_help_dialog_content(qapp):
    from geomodel3d.ui.dialogs.help import HelpDialog

    dialog = HelpDialog()
    text = dialog._browser.toPlainText()
    assert "快速开始" in text
    assert "geomodel3d.engines" in text
    assert "Ctrl+Shift+N" in text


def test_help_action_opens_dialog(window, monkeypatch):
    import geomodel3d.ui.main_window as main_window_module

    captured = {}

    class _FakeHelpDialog:
        def __init__(self, parent=None):
            captured["parent"] = parent

        def exec(self):
            captured["exec"] = True
            return 0

    monkeypatch.setattr(main_window_module, "HelpDialog", _FakeHelpDialog)
    assert window.act_help.shortcut().toString() == "F1"
    window._show_help()
    assert captured["parent"] is window
    assert captured["exec"] is True


def test_project_password_action(window, monkeypatch):
    import geomodel3d.ui.main_window as main_window_module

    window._new_project()
    monkeypatch.setattr(
        main_window_module.QInputDialog,
        "getText",
        staticmethod(lambda *args, **kwargs: ("secret", True)),
    )
    window._set_project_password()
    assert window.ctx.projects.password == "secret"

    monkeypatch.setattr(
        main_window_module.QInputDialog,
        "getText",
        staticmethod(lambda *args, **kwargs: ("", True)),
    )
    window._set_project_password()
    assert window.ctx.projects.password is None


def test_theme_toggle(window):
    initial = window.ctx.settings.theme
    window._toggle_theme()
    assert window.ctx.settings.theme != initial


def test_theme_switch_updates_palette_and_parameter_panel(window):
    from PyQt6.QtGui import QPalette
    from PyQt6.QtWidgets import QApplication

    window.ctx.set_theme("dark")
    app = QApplication.instance()
    palette = app.palette()
    assert palette.color(QPalette.ColorRole.Window).name() == "#1e242c"
    assert palette.color(QPalette.ColorRole.Base).name() == "#232b34"
    assert palette.color(QPalette.ColorRole.Text).name() == "#e3e9f0"

    window.ctx.set_theme("light")
    palette = app.palette()
    assert palette.color(QPalette.ColorRole.Window).name() == "#f5f7fa"
    assert palette.color(QPalette.ColorRole.Base).name() == "#ffffff"

    assert window.params.objectName() == "parameterPanel"
    assert window.params.widget().objectName() == "parameterContainer"
    assert window.params.viewport().autoFillBackground() is False


def test_theme_stylesheets_cover_parameter_panel_and_slider():
    from geomodel3d.app.theme import load_stylesheet

    for theme in ("light", "dark"):
        qss = load_stylesheet(theme)
        assert "#parameterContainer" in qss
        assert "QSlider::groove:horizontal" in qss
        assert "QComboBox QAbstractItemView" in qss
        assert "QScrollArea > QWidget > QWidget" in qss
        assert "#viewportOverlay" in qss
        assert "#viewportOverlayButton" in qss


def test_parameter_panel_faults(window):
    window._new_project()
    project = window.ctx.projects.project
    project.config.project.bounds = Bounds(min=[0, 0, 0], max=[200, 200, 200])
    project.faults = [
        Fault(fault_id="F1", trace=[[0, 0, 0], [100, 0, 0]], params=FaultParams())
    ]
    window.params.set_faults(project.faults)
    assert window.params.current_fault().fault_id == "F1"


def test_fault_cut_signal_from_panel(window):
    window._new_project()
    project = window.ctx.projects.project
    project.config.project.bounds = Bounds(min=[0, 0, 0], max=[200, 200, 200])
    project.faults = [
        Fault(fault_id="F1", trace=[[0, 100, 0], [200, 100, 0]], params=FaultParams())
    ]
    window.params.set_faults(project.faults)
    captured = []
    window.params.faultCutRequested.connect(captured.append)
    window.params._emit_cut()
    assert captured == ["F1"]


def test_create_fault_from_panel(window):
    window._new_project()
    project = window.ctx.projects.project
    project.config.project.bounds = Bounds(min=[0, 0, 0], max=[200, 200, 200])
    window.params.set_faults([])
    assert window.params._fault_hint.isVisibleTo(window.params)
    window._create_fault()
    assert len(project.faults) == 1
    assert window.params._fault_selector.count() == 1
    assert window.params.current_fault() is not None


def test_reserve_and_section_dialogs(qapp, tmp_path):
    from geomodel3d.config.schema import StratigraphyEntry
    from geomodel3d.services.modeling_service import spec_from_project
    from geomodel3d.ui.dialogs.reserve_dialog import ReserveDialog
    from geomodel3d.ui.dialogs.section_dialog import SectionDialog
    from geomodel3d.ui.dialogs.stats_dialog import StatsDialog

    context = AppContext(AppSettings(theme="light"), log_dir=tmp_path / "logs", enable_tasks=False)
    project = context.projects.new_project("demo", bounds=Bounds(min=[0, 0, 0], max=[200, 200, 200]))
    project.config.stratigraphy = [
        StratigraphyEntry(layer_code="A", order=0),
        StratigraphyEntry(layer_code="B", order=1),
    ]
    csv = tmp_path / "bh.csv"
    csv.write_text(
        "孔号,东坐标,北坐标,孔口高程,层顶深,层底深,地层代码,品位\n"
        "H1,0,0,100,0,20,A,1.2\nH1,0,0,100,20,50,B,2.4\n"
        "H2,180,180,120,0,25,A,0.9\nH2,180,180,120,25,60,B,1.6\n",
        encoding="utf-8-sig",
    )
    context.data.import_boreholes(csv)
    context.modeling.create_run(spec_from_project(project, engine="mock", resolution=(8, 8, 6)), async_run=False)

    reserve = ReserveDialog(context)
    reserve._run()
    assert reserve._result is not None
    assert reserve._table.rowCount() >= 1

    from geomodel3d.app.events import RESERVE_SENSITIVITY

    sensitivity = []
    context.bus.subscribe(RESERVE_SENSITIVITY, lambda event: sensitivity.append(event.payload))
    reserve._sensitivity()
    assert sensitivity and sensitivity[-1]["rows"]

    section = SectionDialog(context)
    section._run()
    assert section.results
    target = tmp_path / "section_export.svg"
    count = section._export_all([(section.results[0], target, "svg")])
    assert count == 1 and target.exists()

    stats = StatsDialog(context)
    assert stats._payload
    stats._finish_charts([], str(tmp_path))
    assert "已导出 0 张图表" in stats._summary.text()


def test_repair_dialog_options_and_run(qapp, tmp_path):
    from geomodel3d.config.schema import StratigraphyEntry
    from geomodel3d.services.modeling_service import spec_from_project
    from geomodel3d.ui.dialogs.repair_dialog import RepairDialog

    context = AppContext(AppSettings(theme="light"), log_dir=tmp_path / "logs", enable_tasks=False)
    project = context.projects.new_project("demo", bounds=Bounds(min=[0, 0, 0], max=[200, 200, 200]))
    project.config.stratigraphy = [
        StratigraphyEntry(layer_code="A", order=0),
        StratigraphyEntry(layer_code="B", order=1),
    ]
    csv = tmp_path / "bh.csv"
    csv.write_text(
        "孔号,东坐标,北坐标,孔口高程,层顶深,层底深,地层代码,品位\n"
        "H1,0,0,100,0,20,A,1.2\nH1,0,0,100,20,50,B,2.4\n"
        "H2,180,180,120,0,25,A,0.9\nH2,180,180,120,25,60,B,1.6\n",
        encoding="utf-8-sig",
    )
    context.data.import_boreholes(csv)
    context.modeling.create_run(
        spec_from_project(project, engine="mock", resolution=(8, 8, 6)), async_run=False
    )

    dialog = RepairDialog(context)
    dialog._fix_layer_order.setChecked(True)
    dialog._merge_layers.setChecked(True)
    dialog._simplify.setValue(0.5)
    dialog._smooth.setValue(1)
    options = dialog._options()
    assert options.fill_holes is True
    assert options.fix_layer_order is True
    assert options.merge_layers is True
    assert options.simplify_tolerance == 0.5
    assert options.smooth_iterations == 1

    dialog._run()
    assert dialog.report is not None
    assert dialog._run_btn.isEnabled()


def test_viewport_interactions_no_plotter(window):
    """无真实视口时各交互入口不应抛异常（空态健壮性）。"""
    window._new_project()
    window.viewport.set_layer_visible("A", False)
    window.viewport.set_layer_visible("A", True)
    window.viewport.set_pick_enabled(True)
    window.viewport.set_pick_enabled(False)
    window.viewport.add_measurement()
    window.viewport.set_clip_enabled(True)
    window.viewport.set_clip_enabled(False)
    window.viewport.set_box_pick_enabled(True)
    window.viewport.set_box_pick_enabled(False)
    window.viewport.set_clip_fraction("z", 0.5)
    window.viewport.set_clip_fraction("x", 0.0)
    window.viewport.set_vertical_exaggeration(0.0)
    window.viewport.set_vertical_exaggeration(3.0)


def test_auto_vertical_exaggeration():
    from geomodel3d.ui.widgets.viewport import auto_vertical_exaggeration

    # 薄板模型（水平 2km、垂向 200m）应自动放大：0.3*2000/200 = 3.0
    assert auto_vertical_exaggeration([0, 2000, 0, 2000, 0, 200]) == pytest.approx(3.0)
    # 大面积薄层（水平 50km、垂向 300m）应大幅放大（旧上限 10 会显得扁平）
    assert auto_vertical_exaggeration([0, 50000, 0, 50000, 0, 300]) == pytest.approx(50.0)
    # 高大的模型不应被缩小
    assert auto_vertical_exaggeration([0, 100, 0, 100, 0, 1000]) == 1.0


def test_display_xy_scale():
    import math

    from geomodel3d.ui.widgets.viewport import display_xy_scale

    sx, sy, xc, yc = display_xy_scale([-95.5, -95.0, 37.9, 38.2, 0.0, 100.0])
    assert sx == pytest.approx(111320.0 * math.cos(math.radians(38.05)), rel=1e-6)
    assert sy == 111320.0
    assert (xc, yc) == pytest.approx((-95.25, 38.05))
    # 投影坐标应恒等
    assert display_xy_scale([0, 1000, 0, 2000, 0, 100]) == (1.0, 1.0, 0.0, 0.0)


class _FakeCamera:
    def __init__(self):
        self.zooms = []

    def zoom(self, factor):
        self.zooms.append(factor)


class _FakeTextProperty:
    def __init__(self):
        self.font_size = None
        self.color = None

    def SetFontSize(self, size):
        self.font_size = size

    def SetColor(self, *color):
        self.color = color


class _FakeLegend:
    def __init__(self):
        self.prop = _FakeTextProperty()

    def GetEntryTextProperty(self):
        return self.prop


class _RichFakePlotter:
    """覆盖视口全部交互入口的假 plotter。"""

    def __init__(self):
        self.renderer = type("R", (), {"actors": {}})()
        self.calls = []
        self.last_style = None
        self.last_edges = None
        self.camera = _FakeCamera()
        self.legend = None

    def clear(self):
        self.calls.append("clear")
        self.renderer.actors.clear()

    def add_mesh(self, mesh=None, **kwargs):
        name = kwargs.get("name")
        self.calls.append(("add_mesh", name))
        if kwargs.get("style"):
            self.last_style = kwargs["style"]
        self.last_edges = kwargs.get("show_edges")
        if name:
            self.renderer.actors[name] = _FakeActor()
        return self.renderer.actors.get(name)

    def add_legend(self, labels=None, **kwargs):
        self.calls.append(("add_legend", len(labels or [])))
        self.legend = _FakeLegend()
        return self.legend

    def add_mesh_clip_plane(self, mesh=None, **kwargs):
        self.calls.append(("clip_plane", kwargs.get("name")))
        return None

    def add_points(self, points=None, **kwargs):
        name = kwargs.get("name")
        self.calls.append(("add_points", name))
        if name:
            self.renderer.actors[name] = _FakeActor()
        return self.renderer.actors.get(name)

    def show_bounds(self, **kwargs):
        self.calls.append(("show_bounds", kwargs.get("use_3d_text")))

    def reset_camera(self):
        self.calls.append("reset_camera")

    def render(self):
        self.calls.append("render")

    def set_background(self, *args, **kwargs):
        self.calls.append("background")

    def set_scale(self, **kwargs):
        self.calls.append(("set_scale", kwargs.get("zscale")))

    def disable_picking(self):
        self.calls.append("disable_picking")

    def enable_cell_picking(self, **kwargs):
        self.calls.append("enable_cell_picking")

    def enable_rectangle_picking(self, **kwargs):
        self.calls.append("enable_rectangle_picking")

    def clear_measure_widgets(self):
        self.calls.append("clear_measure")

    def add_measurement_widget(self, **kwargs):
        self.calls.append("add_measurement")

    def screenshot(self, path):
        self.calls.append(("screenshot", path))


class _FakeActor:
    def __init__(self):
        self.visible = True

    def SetVisibility(self, value):
        self.visible = bool(value)

    def GetVisibility(self):
        return self.visible


class _FakePicked:
    def __init__(self, layer_id=0, n_cells=1):
        self.n_cells = n_cells
        self.cell_data = {"layer": [layer_id]} if layer_id is not None else {}
        self.points = __import__("numpy").array([[1.0, 2.0, 3.0]])

    def cell_centers(self):
        import numpy as np

        return type("C", (), {"points": np.array([[1.0, 2.0, 3.0]])})()


def test_viewport_all_interactions(qapp, tmp_path):
    from geomodel3d.algorithms.benchmark import synthetic_spec
    from geomodel3d.engine.mock import MockEngine
    from geomodel3d.ui.widgets.viewport import ViewportWidget

    class _Ctx:
        def progress(self, percent, stage=""):
            return None

        def raise_if_cancelled(self):
            return None

    spec = synthetic_spec((5, 5, 4))
    mesh = MockEngine().build(spec, _Ctx())
    view = ViewportWidget()
    view._plotter = _RichFakePlotter()
    view._plotter.interactor = view._empty

    # 表面模式：生成光滑层实体 + 钻孔/断层参考数据
    from geomodel3d.domain.entities import Drillhole, DrillholeInterval, Fault, FaultParams

    code0 = spec.stratigraphy[0].layer_code
    holes = [
        Drillhole(
            hole_id="H1", x=0.0, y=0.0, collar_elev=100.0,
            intervals=[DrillholeInterval(layer_code=code0, depth_top=0.0, depth_bottom=10.0)],
        )
    ]
    faults = [Fault(fault_id="F1", trace=[[0, 0, 0], [100, 0, 0]], params=FaultParams())]
    view.display_mesh(mesh, spec.stratigraphy, holes, faults)
    names = [c[1] for c in view._plotter.calls if isinstance(c, tuple) and c[0] == "add_mesh"]
    assert any(n and n.startswith("layer_") for n in names)
    assert "borehole_collars" in [c[1] for c in view._plotter.calls if isinstance(c, tuple) and c[0] == "add_points"]
    assert any(n and n.startswith("bore_") for n in names)
    assert any(n and n.startswith("fault_") for n in names)
    assert any(isinstance(c, tuple) and c[0] == "add_legend" for c in view._plotter.calls)
    assert view._plotter.legend is not None
    assert view._plotter.legend.prop.font_size == 9
    # 坐标轴文字必须为 2D（避免被纵向放大拉伸成巨字）
    assert any(c == ("show_bounds", False) for c in view._plotter.calls)

    # 右下角悬浮工具：全屏 + 浮窗
    assert not view._overlay.isHidden()
    assert view._fullscreen_btn.objectName() == "viewportOverlayButton"
    assert view._popout_btn.objectName() == "viewportOverlayButton"
    fullscreen_calls = []
    popout_calls = []
    view.fullscreenRequested.connect(lambda: fullscreen_calls.append(True))
    view.popoutRequested.connect(lambda: popout_calls.append(True))
    view._fullscreen_btn.click()
    view._popout_btn.click()
    assert fullscreen_calls == [True]
    assert popout_calls == [True]

    # 参考数据显隐切换
    view.show_reference(boreholes=False)
    assert view._show_boreholes is False
    view.show_reference(boreholes=True)

    # 图层显隐在重渲染后仍保留
    first_code = spec.stratigraphy[0].layer_code
    view.set_layer_visible(first_code, False)
    assert view._hidden_layers == {first_code}
    view.set_vertical_exaggeration(5.0)
    actor = view._plotter.renderer.actors.get(f"layer_{first_code}")
    assert actor is not None and actor.visible is False

    # 线框模式应使用 wireframe 样式；网格模式应显示边（先关参考数据，避免其线渲染干扰）
    view.show_reference(boreholes=False, faults=False)
    view.set_display_mode("wireframe")
    assert view._plotter.last_style == "wireframe"
    view.set_display_mode("mesh")
    assert view._plotter.last_edges is True

    # 实体模式
    view.set_display_mode("volume")
    assert any(c == ("add_mesh", f"layer_{first_code}") or (isinstance(c, tuple) and c[1] == f"layer_{first_code}") for c in view._plotter.calls)

    # 静态剖切
    view.set_clip_fraction("z", 0.5)
    view.set_clip_fraction("x", 0.0)

    # 拾取开关 + 拾取解析
    captured = []
    view.picked.connect(lambda info: captured.append(info))
    view.set_pick_enabled(True)
    assert "enable_cell_picking" in view._plotter.calls
    pick_id = view._register_pick("N2", "layer")
    view._on_pick(_FakePicked(pick_id))
    assert captured and captured[-1]["layer"] == "N2" and captured[-1]["cells"] == 1
    assert captured[-1]["kind"] == "layer"
    view.set_pick_enabled(False)
    assert "disable_picking" in view._plotter.calls

    # 框选开关
    view.set_box_pick_enabled(True)
    assert "enable_rectangle_picking" in view._plotter.calls
    view.set_box_pick_enabled(False)

    # 测量
    view.add_measurement()
    assert "add_measurement" in view._plotter.calls

    # 交互式剖切开/关
    view.set_clip_enabled(True)
    assert ("clip_plane", "clip") in view._plotter.calls
    view.set_clip_enabled(False)

    # 截图与清空
    view.screenshot(str(tmp_path / "v.png"))
    assert any(isinstance(c, tuple) and c[0] == "screenshot" for c in view._plotter.calls)
    view.clear()
    assert view._mesh is None


def test_clip_plane_from_bounds():
    from geomodel3d.ui.widgets.viewport import clip_plane_from_bounds

    bounds = [0, 100, 0, 200, 0, 300]
    normal, origin = clip_plane_from_bounds(bounds, "x", 0.5)
    assert normal.tolist() == [1.0, 0.0, 0.0]
    assert origin.tolist() == [50.0, 0.0, 0.0]
    normal, origin = clip_plane_from_bounds(bounds, "z", 0.25)
    assert origin.tolist() == [0.0, 0.0, 75.0]


def test_viewport_render_with_fake_plotter(qapp):
    """用假 plotter 覆盖渲染路径，防止 PyVista API 误用（如 add_bounds_axes）。"""
    import numpy as np

    from geomodel3d.config.schema import StratigraphyEntry
    from geomodel3d.engine.umesh import VTK_HEXAHEDRON, UMesh
    from geomodel3d.ui.widgets.viewport import ViewportWidget

    class _FakeActor:
        def __init__(self):
            self.visible = True

        def SetVisibility(self, value):
            self.visible = bool(value)

        def GetVisibility(self):
            return self.visible

    class _FakeRenderer:
        def __init__(self):
            self.actors = {}

    class _FakePlotter:
        def __init__(self):
            self.renderer = _FakeRenderer()
            self.calls = []

        def clear(self):
            self.calls.append("clear")
            self.renderer.actors.clear()

        def add_mesh(self, *args, **kwargs):
            name = kwargs.get("name")
            self.calls.append(("add_mesh", name))
            if name:
                self.renderer.actors[name] = _FakeActor()

        def show_bounds(self, **kwargs):
            self.calls.append("show_bounds")

        def reset_camera(self):
            self.calls.append("reset_camera")

        def render(self):
            self.calls.append("render")

        def set_background(self, *args, **kwargs):
            pass

        def disable_picking(self):
            pass

    vertices = np.array(
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
        dtype=float,
    )
    mesh = UMesh(
        vertices=vertices,
        cells=[np.arange(8)],
        cell_types=np.array([VTK_HEXAHEDRON]),
        face_groups={"A": [0]},
    )
    view = ViewportWidget()
    view._plotter = _FakePlotter()
    view._plotter.interactor = view._empty  # 当前栈页面，避免 setCurrentWidget 触发
    view.display_mesh(mesh, [StratigraphyEntry(layer_code="A", order=0)])
    assert "show_bounds" in view._plotter.calls
    assert ("add_mesh", "layer_A") in view._plotter.calls
    # 图层显隐应真实改变 actor 可见性
    actor = view._plotter.renderer.actors["layer_A"]
    assert actor.visible is True
    view.set_layer_visible("A", False)
    assert actor.visible is False
    view.set_layer_visible("A", True)
    assert actor.visible is True


def test_viewport_surface_mode(qapp):
    """表面模式应渲染光滑层实体（或回退到层界面曲面），而非体元方块。"""
    from geomodel3d.algorithms.benchmark import synthetic_spec
    from geomodel3d.engine.mock import MockEngine
    from geomodel3d.ui.widgets.viewport import ViewportWidget

    class _Ctx:
        def progress(self, percent, stage=""):
            return None

        def raise_if_cancelled(self):
            return None

    class _FakeRenderer:
        def __init__(self):
            self.actors = {}

    class _FakePlotter:
        def __init__(self):
            self.renderer = _FakeRenderer()
            self.calls = []

        def clear(self):
            self.calls.append("clear")

        def add_mesh(self, *args, **kwargs):
            self.calls.append(("add_mesh", kwargs.get("name")))

        def show_bounds(self, **kwargs):
            self.calls.append("show_bounds")

        def reset_camera(self):
            pass

        def render(self):
            pass

        def disable_picking(self):
            pass

    spec = synthetic_spec((4, 4, 4))
    mesh = MockEngine().build(spec, _Ctx())
    view = ViewportWidget()
    view._plotter = _FakePlotter()
    view._plotter.interactor = view._empty
    view.set_display_mode("surface")
    view.display_mesh(mesh, spec.stratigraphy)
    names = [call[1] for call in view._plotter.calls if isinstance(call, tuple)]
    assert any(name and (name.startswith("layer_") or name.startswith("surface_")) for name in names)
    assert any(name and name.startswith("layer_") for name in names)


def test_format_pick_info():
    from geomodel3d.ui.widgets.viewport import format_pick_info

    text = format_pick_info({"x": 1.234, "y": 2.0, "z": 3.0, "layer": 2, "cells": 5})
    assert "地层#2" in text and "单元 5" in text


def test_snapshot_dialog(qapp, tmp_path):
    from geomodel3d.ui.dialogs.snapshot_dialog import SnapshotDialog

    context = AppContext(AppSettings(theme="light"), log_dir=tmp_path / "logs", enable_tasks=False)
    context.projects.new_project("demo", bounds=Bounds(min=[0, 0, 0], max=[200, 200, 200]))
    context.projects.snapshot("v1")
    dialog = SnapshotDialog(context)
    assert dialog._table.rowCount() == 1
    dialog._create()
    assert dialog._table.rowCount() == 2


def test_reserve_chart_view(qapp):
    from geomodel3d.domain.entities import ReserveClass
    from geomodel3d.domain.reserve import ReserveClassSummary, ReserveMetrics, ReserveResult
    from geomodel3d.ui.widgets.reserve_chart_view import ReserveChartView

    result = ReserveResult(
        body_id="ore",
        method="volume",
        metrics=ReserveMetrics(),
        total_tonnage=1000.0,
        total_metal=25.0,
        by_class={
            "331": ReserveClassSummary(reserve_class=ReserveClass.MEASURED, tonnage=600.0, metal=18.0),
            "332": ReserveClassSummary(reserve_class=ReserveClass.INDICATED, tonnage=400.0, metal=7.0),
        },
    )
    view = ReserveChartView()
    view.display(result)
    view.display_sensitivity(
        [{"value": 0.5, "tonnage": 900.0, "metal": 20.0}, {"value": 0.9, "tonnage": 700.0, "metal": 16.0}]
    )
    view.clear()


def test_import_wizard_crs_suggestion(qapp, tmp_path):
    from geomodel3d.ui.wizards.import_wizard import ImportBoreholeWizard

    csv = tmp_path / "bh.csv"
    csv.write_text(
        "孔号,东坐标,北坐标,孔口高程,层顶深,层底深,地层代码\n"
        "H1,116.3,39.9,50,0,20,A\nH2,116.4,39.8,55,0,22,A\n",
        encoding="utf-8-sig",
    )
    wizard = ImportBoreholeWizard()
    assert wizard.load_path(csv)
    # 经纬度坐标应被自动识别并在 CRS 页预选
    assert wizard._crs_page.epsg() in (4490, 4326)

