"""主窗口（7.3 / 7.4）。

QDockWidget 可停靠分区 + 中央多 Tab；菜单/工具栏/快捷键按 7.4 规范；
所有业务交互经 AppContext 服务层与事件总线完成。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QDialog,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from geomodel3d import __version__
from geomodel3d.app.context import AppContext
from geomodel3d.app.events import (
    DATA_READY,
    ERROR_RAISED,
    LOG_MESSAGE,
    MODEL_CHANGED,
    PROJECT_MODIFIED,
    PROJECT_OPENED,
    RESERVE_READY,
    RESERVE_SENSITIVITY,
    RUN_FAILED,
    RUN_FINISHED,
    RUN_PROGRESS,
    RUN_STARTED,
    SECTION_READY,
    THEME_CHANGED,
)
from geomodel3d.app.qt_bridge import QtEventBridge
from geomodel3d.app.theme import apply_theme
from geomodel3d.config.defaults import APP_DISPLAY_NAME, PROJECT_SUFFIX
from geomodel3d.dataio.encryption import is_encrypted
from geomodel3d.services.modeling_service import spec_from_project
from geomodel3d.ui.dialogs.about import AboutDialog
from geomodel3d.ui.dialogs.error_dialog import ErrorDialog
from geomodel3d.ui.dialogs.help import HelpDialog
from geomodel3d.ui.dialogs.repair_dialog import RepairDialog
from geomodel3d.ui.dialogs.reserve_dialog import ReserveDialog
from geomodel3d.ui.dialogs.section_dialog import SectionDialog
from geomodel3d.ui.dialogs.snapshot_dialog import SnapshotDialog
from geomodel3d.ui.dialogs.stats_dialog import StatsDialog
from geomodel3d.ui.widgets.log_panel import LogPanel
from geomodel3d.ui.widgets.parameter_panel import ParameterPanel
from geomodel3d.ui.widgets.project_tree import ProjectTree
from geomodel3d.ui.widgets.reserve_chart_view import ReserveChartView
from geomodel3d.ui.widgets.section_view import SectionView
from geomodel3d.ui.widgets.table_panel import DataTablePanel
from geomodel3d.ui.widgets.viewport import ViewportWidget, format_pick_info
from geomodel3d.ui.wizards.import_wizard import ImportBoreholeWizard

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.ctx = context
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.resize(1440, 900)
        self.setDockNestingEnabled(True)

        self._build_central()
        self._viewport_window = None
        self._viewport_fullscreen = False
        self._saved_window_state = None
        self._saved_dock_visibility = (True, True, True)
        self._saved_menu_visible = True
        self._saved_toolbar_visible = True
        self._saved_status_visible = True
        self._saved_tabbar_visible = True
        self._saved_tab_index = 0
        self.viewport.fullscreenRequested.connect(self._toggle_viewport_fullscreen)
        self.viewport.popoutRequested.connect(self._open_viewport_window)
        self._build_docks()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_statusbar()

        self._fullscreen_shortcut = QShortcut(QKeySequence("Esc"), self)
        self._fullscreen_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._fullscreen_shortcut.setEnabled(False)
        self._fullscreen_shortcut.activated.connect(self._exit_viewport_fullscreen)

        self._bridge = QtEventBridge(context.bus)
        self._bridge.event.connect(self._on_event)

        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._autosave)
        self._configure_autosave()

        apply_theme(self._app(), context.settings.theme)
        self._refresh_recent_menu()
        self._refresh_title()

        if not (
            os.environ.get("GEOMODEL3D_SELF_TEST")
            or os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen")
        ):
            QTimer.singleShot(0, self._check_orphan_autosaves)

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _app(self):
        from PyQt6.QtWidgets import QApplication

        return QApplication.instance()

    def _build_central(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.viewport = ViewportWidget()
        self.data_table = DataTablePanel()
        self.section_view = SectionView()
        self.reserve_view = ReserveChartView()
        self.tabs.addTab(self.viewport, "三维视图")
        self.tabs.addTab(self.section_view, "剖面视图")
        self.tabs.addTab(self.reserve_view, "储量图表")
        self.tabs.addTab(self.data_table, "数据表格")
        self.setCentralWidget(self.tabs)

    def _build_docks(self) -> None:
        self.tree = ProjectTree()
        self.tree.itemActivated.connect(self._on_tree_activated)
        self.tree.visibilityChanged.connect(self.viewport.set_layer_visible)
        self.tree.refreshRequested.connect(lambda: self.tree.refresh(self.ctx.projects.project))
        self.tree.exportRequested.connect(self._export)
        self.tree.zoomRequested.connect(self.viewport.reset_view)
        self.left_dock = _dock("工程 / 图层", self.tree)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.left_dock)

        self.params = ParameterPanel()
        self.params.faultApplied.connect(self._apply_fault)
        self.params.faultPreview.connect(self._preview_fault)
        self.params.faultCutRequested.connect(self._cut_fault)
        self.params.faultReset.connect(lambda _: self._load_project_into_ui())
        self.params.faultCreateRequested.connect(self._create_fault)
        self.params.interpChanged.connect(self._on_interp_changed)
        self.params.reserveChanged.connect(self._on_reserve_changed)
        self.params.themeToggleRequested.connect(self._toggle_theme)
        self.params.resetViewRequested.connect(self.viewport.reset_view)
        self.params.clipChanged.connect(self.viewport.set_clip_fraction)
        self.params.exaggerationChanged.connect(self.viewport.set_vertical_exaggeration)
        self.right_dock = _dock("参数面板", self.params)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.right_dock)

        self.log_panel = LogPanel()
        self.bottom_dock = _dock("日志", self.log_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.bottom_dock)

        if self.ctx.tasks is not None:
            self.ctx.tasks.taskFinished.connect(self._on_task_finished)
            self.ctx.tasks.taskFailed.connect(self._on_task_failed)

    def _build_actions(self) -> None:
        self.act_new = _action("新建工程", "Ctrl+N", self._new_project)
        self.act_open = _action("打开工程…", "Ctrl+O", self._open_project)
        self.act_save = _action("保存工程", "Ctrl+S", self._save_project)
        self.act_save_as = _action("另存为…", "Ctrl+Shift+S", self._save_project_as)
        self.act_password = _action("设置/清除工程密码…", "", self._set_project_password)
        self.act_import_boreholes = _action("导入钻孔…", "", self._import_boreholes)
        self.act_import_faults = _action("导入断层…", "", self._import_faults)
        self.act_import_seismic = _action("导入地震趋势…", "", self._import_seismic)
        self.act_exit = _action("退出", "Ctrl+Q", self.close)

        self.act_undo = _action("撤销", "Ctrl+Z", self._undo)
        self.act_redo = _action("重做", "Ctrl+Y", self._redo)
        self.act_snapshot = _action("创建版本快照", "Ctrl+Shift+N", self._create_snapshot)
        self.act_history = _action("版本历史…", "", self._show_history)

        self.act_quick_build = _action("快速建模", "", self._quick_build)
        self.act_high_build = _action("高精度建模", "", self._high_build)
        self.act_model_health = _action("模型体检 / 修复", "", self._model_health)
        self.act_advanced_repair = _action("高级模型修复…", "", self._advanced_repair)
        self.act_reset_view = _action("复位视图", "R", self.viewport.reset_view)
        self.act_pick = QAction("拾取查询", self)
        self.act_pick.setCheckable(True)
        self.act_pick.setShortcut(QKeySequence("Ctrl+P"))
        self.act_pick.toggled.connect(self.viewport.set_pick_enabled)
        self.act_box_pick = QAction("框选查询", self)
        self.act_box_pick.setCheckable(True)
        self.act_box_pick.setShortcut(QKeySequence("F"))
        self.act_box_pick.toggled.connect(self.viewport.set_box_pick_enabled)
        self.act_measure = QAction("测量", self)
        self.act_measure.triggered.connect(self.viewport.add_measurement)
        self.act_clip = QAction("剖切面", self)
        self.act_clip.setCheckable(True)
        self.act_clip.toggled.connect(self.viewport.set_clip_enabled)
        self.viewport.picked.connect(self._on_picked)

        self.act_mode_surface = QAction("表面模式（层界面曲面）", self)
        self.act_mode_volume = QAction("实体模式（体元）", self)
        self.act_mode_wire = QAction("线框模式", self)
        self.act_mode_mesh = QAction("网格模式（体元+边）", self)
        self._mode_group = QActionGroup(self)
        self._mode_group.setExclusive(True)
        for action, mode in (
            (self.act_mode_surface, "surface"),
            (self.act_mode_volume, "volume"),
            (self.act_mode_wire, "wireframe"),
            (self.act_mode_mesh, "mesh"),
        ):
            action.setCheckable(True)
            action.setData(mode)
            action.triggered.connect(lambda _checked, m=mode: self.viewport.set_display_mode(m))
            self._mode_group.addAction(action)
        self.act_mode_surface.setChecked(True)
        self.act_theme = _action("切换主题", "", self._toggle_theme)
        self.act_reset_layout = _action("恢复默认布局", "", self._reset_layout)
        self.act_about = _action("关于", "", self._show_about)
        self.act_help = _action("帮助", "F1", self._show_help)

        self.act_reserve = _action("储量计算…", "", self._reserve)
        self.act_section = _action("剖面向导…", "", self._section)
        self.act_export = _action("导出成果…", "", self._export)
        self.act_stats = _action("统计分析", "", self._stats)
        self.act_quality = _action("数据质量报告", "", self._show_quality)

        for action in (self.act_save, self.act_save_as, self.act_password,
                       self.act_undo, self.act_redo,
                       self.act_quick_build, self.act_high_build, self.act_reserve,
                       self.act_section, self.act_export, self.act_stats,
                       self.act_model_health, self.act_advanced_repair):
            action.setEnabled(False)

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("文件(&F)")
        file_menu.addActions([self.act_new, self.act_open, self.act_save, self.act_save_as])
        file_menu.addAction(self.act_password)
        file_menu.addSeparator()
        file_menu.addActions([self.act_import_boreholes, self.act_import_faults, self.act_import_seismic])
        self.recent_menu = file_menu.addMenu("最近工程")
        file_menu.addSeparator()
        file_menu.addAction(self.act_exit)

        edit_menu = bar.addMenu("编辑(&E)")
        edit_menu.addActions([self.act_undo, self.act_redo])
        edit_menu.addSeparator()
        edit_menu.addActions([self.act_snapshot, self.act_history])

        view_menu = bar.addMenu("视图(&V)")
        view_menu.addAction(self.left_dock.toggleViewAction())
        view_menu.addAction(self.right_dock.toggleViewAction())
        view_menu.addAction(self.bottom_dock.toggleViewAction())
        view_menu.addSeparator()
        view_menu.addActions(
            [self.act_reset_view, self.act_pick, self.act_box_pick, self.act_measure, self.act_clip]
        )
        view_menu.addSeparator()
        view_menu.addActions([self.act_mode_surface, self.act_mode_volume, self.act_mode_wire, self.act_mode_mesh])
        view_menu.addSeparator()
        view_menu.addActions([self.act_reset_layout, self.act_theme])

        data_menu = bar.addMenu("数据(&D)")
        data_menu.addActions([self.act_import_boreholes, self.act_import_faults,
                              self.act_import_seismic, self.act_quality])

        model_menu = bar.addMenu("建模(&M)")
        model_menu.addActions([
            self.act_quick_build, self.act_high_build,
            self.act_model_health, self.act_advanced_repair,
        ])

        section_menu = bar.addMenu("剖面(&S)")
        section_menu.addAction(self.act_section)

        compute_menu = bar.addMenu("计算(&C)")
        compute_menu.addActions([self.act_reserve, self.act_stats])

        result_menu = bar.addMenu("成果(&O)")
        result_menu.addAction(self.act_export)

        help_menu = bar.addMenu("帮助(&H)")
        help_menu.addActions([self.act_help, self.act_about])

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("主工具栏")
        self._toolbar = toolbar
        toolbar.setObjectName("main_toolbar")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)
        toolbar.addActions([self.act_new, self.act_open, self.act_save])
        toolbar.addSeparator()
        toolbar.addActions([self.act_import_boreholes, self.act_import_faults, self.act_import_seismic])
        toolbar.addSeparator()
        toolbar.addActions([self.act_quick_build, self.act_high_build])
        toolbar.addSeparator()
        toolbar.addActions([self.act_undo, self.act_redo])

    def _build_statusbar(self) -> None:
        status = QStatusBar()
        self.setStatusBar(status)
        self._status_label = QLabel("就绪")
        self._progress = QProgressBar()
        self._progress.setFixedWidth(220)
        self._progress.setVisible(False)
        status.addWidget(self._status_label, 1)
        status.addPermanentWidget(self._progress)
        self._status_version = QLabel(f"v{__version__}")
        status.addPermanentWidget(self._status_version)

    # ------------------------------------------------------------------ #
    # 事件处理
    # ------------------------------------------------------------------ #
    def _on_event(self, topic: str, payload: object) -> None:
        handler = {
            PROJECT_OPENED: self._on_project_opened,
            PROJECT_MODIFIED: lambda _: self._refresh_title(),
            MODEL_CHANGED: self._on_model_changed,
            RUN_STARTED: self._on_run_started,
            RUN_PROGRESS: self._on_run_progress,
            RUN_FINISHED: self._on_run_finished,
            RUN_FAILED: self._on_run_failed,
            SECTION_READY: self._on_section_ready,
            RESERVE_READY: self._on_reserve_ready,
            RESERVE_SENSITIVITY: self._on_reserve_sensitivity,
            DATA_READY: self._on_data_ready,
            LOG_MESSAGE: self._on_log_message,
            ERROR_RAISED: self._on_error,
            THEME_CHANGED: self._on_theme_changed,
        }.get(topic)
        if handler is not None:
            handler(payload)

    def _on_project_opened(self, project) -> None:
        self._load_project_into_ui()
        self._refresh_title()
        self._set_actions_enabled(True)

    def _load_project_into_ui(self) -> None:
        project = self.ctx.projects.project
        self.tree.refresh(project)
        if project is None:
            self.viewport.clear()
            return
        self.params.set_interp(project.config.interp)
        self.params.set_reserve(project.config.reserve)
        self.params.set_faults(project.faults)
        if project.has_model():
            self.viewport.display_mesh(
                project.mesh, project.config.stratigraphy,
                project.drillholes, project.faults,
            )

    def _on_model_changed(self, payload) -> None:
        project = self.ctx.projects.project
        if project is None or project.mesh is None:
            return
        self.viewport.display_mesh(
            project.mesh, project.config.stratigraphy,
            project.drillholes, project.faults,
        )
        self.tree.refresh(project)
        self.tabs.setCurrentWidget(self.viewport)
        self._status_label.setText(f"模型已更新（{project.mesh.n_cells} 单元）")
        self._set_actions_enabled(True)

    def _on_run_started(self, payload: dict) -> None:
        self._progress.setVisible(True)
        self._progress.setValue(0)
        engine = payload.get("engine", "")
        self._status_label.setText(f"建模任务已提交（{engine}）…")

    def _on_run_progress(self, payload: dict) -> None:
        self._progress.setVisible(True)
        self._progress.setValue(int(payload.get("percent", 0)))
        stage = payload.get("stage", "")
        self._status_label.setText(f"建模中… {payload.get('percent', 0)}% {stage}")

    def _on_run_finished(self, payload: dict) -> None:
        self._progress.setVisible(False)
        self._progress.setValue(0)
        self._status_label.setText(f"建模完成（{payload.get('duration_s', 0)}s）")

    def _on_run_failed(self, payload: dict) -> None:
        self._progress.setVisible(False)
        # 同步链路异常会经 _guard 弹出，此处避免重复弹窗
        if payload.get("handled_by_caller"):
            return
        error = payload.get("error", "未知错误")
        self._show_error("建模失败", str(error))

    def _on_section_ready(self, result) -> None:
        self.section_view.display(result)
        self.tabs.setCurrentWidget(self.section_view)
        self._status_label.setText(f"剖面已生成：{result.plan.name}")

    def _on_reserve_ready(self, result) -> None:
        self.reserve_view.display(result)
        self.tabs.setCurrentWidget(self.reserve_view)
        self._status_label.setText(f"储量计算完成：{result.summary_text()}")

    def _on_reserve_sensitivity(self, payload) -> None:
        rows = payload.get("rows", []) if isinstance(payload, dict) else payload
        field = payload.get("field", "cutoff_grade") if isinstance(payload, dict) else "cutoff_grade"
        self.reserve_view.display_sensitivity(rows, field=field)
        self.tabs.setCurrentWidget(self.reserve_view)

    def _on_data_ready(self, payload: dict) -> None:
        project = self.ctx.projects.project
        self.tree.refresh(project)
        self._status_label.setText(f"已导入 {payload.get('kind')}：{payload.get('count', '')} 条")
        if project is not None:
            self.params.set_faults(project.faults)
            self.viewport.set_reference_data(project.drillholes, project.faults)
            if payload.get("kind") == "boreholes":
                self.data_table.show_boreholes(project.drillholes)
                self.tabs.setCurrentWidget(self.data_table)
            elif payload.get("kind") == "faults":
                self.data_table.show_faults(project.faults)
                self.tabs.setCurrentWidget(self.data_table)

    def _on_log_message(self, payload: dict) -> None:
        self.log_panel.append(
            str(payload.get("level", "INFO")),
            str(payload.get("message", "")),
            str(payload.get("time", "")),
        )

    def _on_error(self, payload: dict) -> None:
        self._show_error(
            payload.get("message", "操作失败"),
            payload.get("suggestion", ""),
            payload.get("code", ""),
            str(payload.get("context", "")),
        )

    def _on_theme_changed(self, theme: str) -> None:
        apply_theme(self._app(), theme)
        self.viewport.set_theme(theme)

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #
    def _new_project(self) -> None:
        self.ctx.projects.new_project("untitled")
        self._load_project_into_ui()
        self._set_actions_enabled(True)

    def _open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "打开工程", "", f"工程文件 (*{PROJECT_SUFFIX});;所有文件 (*.*)"
        )
        if not path:
            return
        if not self._open_project_path(Path(path)):
            return
        candidate = self.ctx.projects.has_recovery()
        if candidate is not None:
            reply = QMessageBox.question(
                self,
                "恢复自动保存",
                f"检测到比工程更新的自动保存文件：\n{candidate}\n\n是否恢复？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._guard(lambda: self.ctx.projects.recover())

    def _open_project_path(self, path: Path) -> bool:
        """打开工程；加密工程先询问密码。"""
        password = None
        if is_encrypted(path):
            password, ok = QInputDialog.getText(
                self,
                "工程密码",
                f"{path.name} 已加密，请输入密码：",
                QLineEdit.EchoMode.Password,
            )
            if not ok:
                return False
        return self._guard(
            lambda: self.ctx.projects.open_project(path, password=password or None)
        )

    def _set_project_password(self) -> None:
        if self.ctx.projects.project is None:
            return
        password, ok = QInputDialog.getText(
            self,
            "设置/清除工程密码",
            "输入工程密码（留空表示清除）：",
            QLineEdit.EchoMode.Password,
        )
        if not ok:
            return
        self.ctx.projects.password = password or None
        self.ctx.projects.touch()
        if password:
            self._status_label.setText("已设置工程密码，保存后生效")
        else:
            self._status_label.setText("已清除工程密码，保存后生效")

    def _save_project(self) -> bool:
        project = self.ctx.projects.project
        if project is None:
            return False
        if project.path is None:
            return self._save_project_as()
        return self._guard(lambda: self.ctx.projects.save())

    def _save_project_as(self) -> bool:
        if self.ctx.projects.project is None:
            return False
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为", f"untitled{PROJECT_SUFFIX}", f"工程文件 (*{PROJECT_SUFFIX})"
        )
        if path:
            ok = self._guard(lambda: self.ctx.projects.save_as(Path(path)))
            self._refresh_recent_menu()
            return ok
        return False

    def _import_boreholes(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择钻孔文件", "", "数据文件 (*.csv *.xlsx *.xls);;所有文件 (*.*)"
        )
        if not path:
            return
        if self.ctx.projects.project is None:
            self._new_project()
        wizard = ImportBoreholeWizard(self, default_crs=self.ctx.settings.default_crs_epsg)
        wizard.load_path(Path(path))
        if wizard.exec() != ImportBoreholeWizard.DialogCode.Accepted:
            return
        file_path, mapping, epsg = wizard.result_config()
        project = self.ctx.projects.project
        if epsg:
            from geomodel3d.config.schema import CRSConfig

            project.config.project.source_crs = CRSConfig(epsg=epsg)
            if project.crs.epsg is None:
                project.config.project.crs = CRSConfig(epsg=epsg)
        self._guard(lambda: self.ctx.data.import_boreholes(file_path, mapping))
        self._load_project_into_ui()

    def _import_faults(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择断层文件", "", "断层文件 (*.shp *.json);;所有文件 (*.*)"
        )
        if not path:
            return
        if self.ctx.projects.project is None:
            self._new_project()
        self._guard(lambda: self.ctx.data.import_faults(Path(path)))
        self._load_project_into_ui()

    def _import_seismic(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择地震趋势面（可多选，SEG-Y 多卷/多线将合并）",
            "",
            "地震数据 (*.asc *.grd *.sgy *.segy *.csv *.txt);;所有文件 (*.*)",
        )
        if not paths:
            return
        if self.ctx.projects.project is None:
            self._new_project()
        self._guard(lambda: self.ctx.data.import_seismic([Path(p) for p in paths]))

    def _quick_build(self) -> None:
        project = self.ctx.projects.project
        if project is None:
            return
        spec = spec_from_project(project, engine=project.config.engine_policy,
                                 purpose="quick_preview", resolution=(48, 48, 32))
        self._guard(lambda: self.ctx.modeling.create_run(spec, async_run=True))

    def _high_build(self) -> None:
        project = self.ctx.projects.project
        if project is None:
            return
        spec = spec_from_project(project, engine="opengeode",
                                 purpose="high_precision", resolution=(64, 64, 48))
        spec.need_high_precision = True
        self._guard(lambda: self.ctx.modeling.create_run(spec, async_run=True))

    def _apply_fault(self, fault_id: str, params) -> None:
        self._guard(lambda: self.ctx.fault_editor.update_fault(fault_id, params, async_run=True))
        self._refresh_title()

    def _create_fault(self) -> None:
        if self.ctx.projects.project is None:
            return
        fault = self.ctx.fault_editor.create_fault()
        self.params.set_faults(self.ctx.projects.project.faults)
        self.tree.refresh(self.ctx.projects.project)
        self._status_label.setText(f"已新建断层 {fault.fault_id}，可在右侧调整参数后点击“应用并重建模”")

    def _preview_fault(self, fault_id: str) -> None:
        try:
            region = self.ctx.fault_editor.preview_impact(fault_id)
            self._status_label.setText(f"影响域: {[round(v, 1) for v in region]}")
        except Exception as exc:  # noqa: BLE001
            self._show_error("预览影响域失败", str(exc))

    def _cut_fault(self, fault_id: str) -> None:
        project = self.ctx.projects.project
        if project is None or not project.has_model():
            return
        reply = QMessageBox.question(
            self,
            "断层几何切割预览",
            "将沿断层面切割模型并平移一盘，替换当前模型。\n"
            "该结果为 VTK 几何切割（两盘封闭），不是 OpenGeode 原生布尔实体；"
            "切割前会自动创建版本快照。\n\n是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self._guard(lambda: self.ctx.fault_editor.cut_fault(fault_id)):
            self._status_label.setText(
                f"断层 {fault_id} 几何切割完成（开放切口预览）"
            )

    def _undo(self) -> None:
        if self.ctx.fault_editor.undo():
            self._load_project_into_ui()
            self._status_label.setText("已撤销")

    def _redo(self) -> None:
        if self.ctx.fault_editor.redo():
            self._load_project_into_ui()
            self._status_label.setText("已重做")

    def _create_snapshot(self) -> None:
        if self.ctx.projects.project is None:
            return
        snapshot = self.ctx.projects.snapshot("手动快照")
        self._status_label.setText(f"已创建版本快照 {snapshot.id}")

    def _show_history(self) -> None:
        if self.ctx.projects.project is None:
            return
        SnapshotDialog(self.ctx, self).exec()
        self._load_project_into_ui()

    def _configure_autosave(self) -> None:
        minutes = self.ctx.settings.autosave_minutes
        if minutes and minutes > 0:
            self._autosave_timer.start(int(minutes * 60 * 1000))
        else:
            self._autosave_timer.stop()

    def _autosave(self) -> None:
        project = self.ctx.projects.project
        if project is None or not project.dirty:
            return
        try:
            path = self.ctx.projects.autosave()
        except Exception as exc:  # noqa: BLE001
            self.ctx.exceptions.report(exc, publish=False)
            self._status_label.setText("自动保存失败（详见日志）")
            return
        if path is not None:
            self._status_label.setText(f"已自动保存：{path.name}")

    def _on_interp_changed(self, config) -> None:
        project = self.ctx.projects.project
        if project is not None:
            # 面板只发出部分字段，合并进现有配置，避免其它字段被重置为默认值
            updates = config.model_dump(exclude_unset=True)
            project.config.interp = project.config.interp.model_copy(update=updates)
            project.mark_dirty()

    def _on_reserve_changed(self, config) -> None:
        project = self.ctx.projects.project
        if project is not None:
            updates = config.model_dump(exclude_unset=True)
            project.config.reserve = project.config.reserve.model_copy(update=updates)
            project.mark_dirty()

    def _toggle_theme(self) -> None:
        new_theme = "dark" if self.ctx.settings.theme == "light" else "light"
        self.ctx.set_theme(new_theme)

    def _on_picked(self, info: dict) -> None:
        self._status_label.setText(format_pick_info(info))

    def _check_orphan_autosaves(self) -> None:
        """启动时检查未保存工程的自动保存文件，提示恢复。"""
        try:
            orphans = self.ctx.projects.orphan_autosaves()
        except Exception as exc:  # noqa: BLE001
            self.ctx.exceptions.report(exc, publish=False)
            return
        if not orphans:
            return
        latest = max(orphans, key=lambda path: path.stat().st_mtime)
        reply = QMessageBox.question(
            self,
            "恢复未保存工程",
            f"检测到 {len(orphans)} 个未保存工程的自动保存文件。\n最近一个：\n{latest}\n\n是否恢复？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            password = None
            if is_encrypted(latest):
                password, ok = QInputDialog.getText(
                    self,
                    "工程密码",
                    "该自动保存已加密，请输入密码：",
                    QLineEdit.EchoMode.Password,
                )
                if not ok:
                    return
            self._guard(
                lambda: self.ctx.projects.recover_from(latest, password=password or None)
            )
            self._set_actions_enabled(True)

    def _reset_layout(self) -> None:
        for dock, area in (
            (self.left_dock, Qt.DockWidgetArea.LeftDockWidgetArea),
            (self.right_dock, Qt.DockWidgetArea.RightDockWidgetArea),
            (self.bottom_dock, Qt.DockWidgetArea.BottomDockWidgetArea),
        ):
            dock.setFloating(False)
            self.addDockWidget(area, dock)
            dock.setVisible(True)

    def _toggle_viewport_fullscreen(self) -> None:
        if self._viewport_fullscreen:
            self._exit_viewport_fullscreen()
        else:
            self._enter_viewport_fullscreen()

    def _enter_viewport_fullscreen(self) -> None:
        """隐藏菜单/工具栏/Dock，让三维视口占满整个应用窗口。"""
        if self._viewport_fullscreen:
            return
        if self._viewport_window is not None:
            self._viewport_window.close()

        self._viewport_fullscreen = True
        self._saved_window_state = self.windowState()
        self._saved_dock_visibility = (
            not self.left_dock.isHidden(),
            not self.right_dock.isHidden(),
            not self.bottom_dock.isHidden(),
        )
        self._saved_menu_visible = not self.menuBar().isHidden()
        self._saved_toolbar_visible = not self._toolbar.isHidden()
        self._saved_status_visible = not self.statusBar().isHidden()
        self._saved_tabbar_visible = not self.tabs.tabBar().isHidden()
        self._saved_tab_index = self.tabs.currentIndex()

        self.tabs.setCurrentWidget(self.viewport)
        self.left_dock.hide()
        self.right_dock.hide()
        self.bottom_dock.hide()
        self.menuBar().hide()
        self._toolbar.hide()
        self.statusBar().hide()
        self.tabs.tabBar().hide()
        self._fullscreen_shortcut.setEnabled(True)
        self.showFullScreen()
        QTimer.singleShot(0, self.viewport.refresh)

    def _exit_viewport_fullscreen(self) -> None:
        """退出全屏并恢复菜单、Dock、状态栏与中央 Tab。"""
        if not self._viewport_fullscreen:
            return
        self._viewport_fullscreen = False
        self._fullscreen_shortcut.setEnabled(False)

        if self._saved_window_state is not None and (
            self._saved_window_state & Qt.WindowState.WindowMaximized
        ):
            self.showMaximized()
        else:
            self.showNormal()

        self.menuBar().setVisible(self._saved_menu_visible)
        self._toolbar.setVisible(self._saved_toolbar_visible)
        self.left_dock.setVisible(self._saved_dock_visibility[0])
        self.right_dock.setVisible(self._saved_dock_visibility[1])
        self.bottom_dock.setVisible(self._saved_dock_visibility[2])
        self.statusBar().setVisible(self._saved_status_visible)
        self.tabs.tabBar().setVisible(self._saved_tabbar_visible)
        self.tabs.setCurrentIndex(self._saved_tab_index)
        QTimer.singleShot(0, self.viewport.refresh)

    def _open_viewport_window(self) -> None:
        if self._viewport_window is not None:
            self._viewport_window.close()
            return
        if self._viewport_fullscreen:
            self._exit_viewport_fullscreen()

        dialog = QDialog(self, Qt.WindowType.Window)
        dialog.setWindowTitle("三维视图 - 浮窗")
        dialog.resize(1000, 760)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        clone = ViewportWidget(dialog)
        clone.set_theme(self.ctx.settings.theme)
        project = self.ctx.projects.project
        if project is not None and project.has_model():
            clone.display_mesh(
                project.mesh,
                project.config.stratigraphy,
                project.drillholes,
                project.faults,
            )
        clone._overlay.hide()
        layout.addWidget(clone)
        self._viewport_window = dialog

        def _closed(*_args) -> None:
            self._viewport_window = None
            clone.shutdown()
            QTimer.singleShot(0, self.viewport.refresh)

        dialog.finished.connect(_closed)
        dialog.show()

    def _show_about(self) -> None:
        AboutDialog(self).exec()

    def _reserve(self) -> None:
        if self.ctx.projects.project is None:
            return
        ReserveDialog(self.ctx, self).exec()

    def _section(self) -> None:
        if self.ctx.projects.project is None:
            return
        dialog = SectionDialog(self.ctx, self)
        if dialog.exec() == SectionDialog.DialogCode.Accepted and dialog.results:
            self.section_view.display(dialog.results[0])
            self.tabs.setCurrentWidget(self.section_view)

    def _export(self) -> None:
        if self.ctx.projects.project is None or not self.ctx.projects.project.has_model():
            QMessageBox.information(self, "导出成果", "请先生成统一模型。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出三维模型",
            "model.vtu",
            "VTU (*.vtu);;OBJ (*.obj);;STL (*.stl);;PLY (*.ply);;GLB (*.glb);;"
            "OpenGeode (*.og_hso3d)",
        )
        if path:
            self._guard(lambda: self.ctx.exporter.export_mesh(Path(path)))
            self._status_label.setText(f"已导出：{path}")

    def _model_health(self) -> None:
        self._guard(self._show_health_report)

    def _stats(self) -> None:
        if self.ctx.projects.project is None:
            return
        StatsDialog(self.ctx, self).exec()

    def _show_health_report(self) -> None:
        report = self.ctx.exporter.health_report()
        summary = report.summary()
        lines = [
            f"错误 {summary['error']} · 告警 {summary['warning']} · 提示 {summary['info']}",
            f"单元 {int(report.metrics.get('cells', 0))} · 顶点 {int(report.metrics.get('points', 0))}",
            f"总体积 {report.metrics.get('volume', 0):,.0f} m³",
        ]
        for issue in report.issues:
            lines.append(f"[{issue.severity.value}] {issue.message}")
        box = QMessageBox(self)
        box.setWindowTitle("模型体检报告")
        box.setText("\n".join(lines))
        repair_btn = box.addButton("自动修复", QMessageBox.ButtonRole.AcceptRole)
        advanced_btn = box.addButton("高级修复…", QMessageBox.ButtonRole.ActionRole)
        box.addButton("关闭", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is repair_btn:
            self._start_repair()
        elif box.clickedButton() is advanced_btn:
            self._advanced_repair()

    def _advanced_repair(self) -> None:
        project = self.ctx.projects.project
        if project is None or not project.has_model():
            return
        RepairDialog(self.ctx, self).exec()
        self.tree.refresh(self.ctx.projects.project)

    def _start_repair(self) -> None:
        """在后台线程执行模型修复，避免大网格阻塞界面。"""
        project = self.ctx.projects.project
        if project is None or not project.has_model():
            return
        self._status_label.setText("正在修复模型…")
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._guard(lambda: self.ctx.repair.repair(async_run=True))

    def _on_task_finished(self, run_id: str, result: object) -> None:
        if not run_id.startswith("repair"):
            return
        self._progress.setVisible(False)
        self._progress.setValue(0)
        summary = result.summary() if hasattr(result, "summary") else "模型修复完成"
        self._status_label.setText(str(summary))
        self.tree.refresh(self.ctx.projects.project)

    def _on_task_failed(self, run_id: str, exc: object) -> None:
        if not run_id.startswith("repair"):
            return
        self._progress.setVisible(False)
        error = self.ctx.exceptions.report(exc, publish=False)
        self._show_error(
            error.message,
            error.suggestion,
            error.code.value,
            self.ctx.exceptions.traceback_text(exc),
        )

    def _show_quality(self) -> None:
        report = self.ctx.data.get_quality_report()
        summary = report.summary()
        QMessageBox.information(
            self,
            "数据质量报告",
            f"错误 {summary['error']} · 告警 {summary['warning']} · 提示 {summary['info']}",
        )

    def _show_help(self) -> None:
        HelpDialog(self).exec()

    # ------------------------------------------------------------------ #
    def _guard(self, fn) -> bool:
        """统一异常捕获 -> 错误对话框；返回是否成功。"""
        try:
            fn()
            return True
        except Exception as exc:  # noqa: BLE001
            error = self.ctx.exceptions.report(exc, publish=False)
            detail = self.ctx.exceptions.traceback_text(exc) + f"\n\n日志文件: {self.ctx.log_file}"
            self._show_error(error.message, error.suggestion, error.code.value, detail)
            return False

    def _show_error(self, message: str, suggestion: str = "", code: str = "", detail: str = "") -> None:
        dialog = ErrorDialog(message, code=code, suggestion=suggestion, detail=detail)
        dialog.exec()
        dialog.deleteLater()

    def _set_actions_enabled(self, enabled: bool) -> None:
        for action in (self.act_save, self.act_save_as, self.act_password,
                       self.act_undo, self.act_redo,
                       self.act_quick_build, self.act_high_build, self.act_reserve,
                       self.act_section, self.act_export, self.act_stats,
                       self.act_model_health, self.act_advanced_repair):
            action.setEnabled(enabled)

    def _refresh_title(self) -> None:
        project = self.ctx.projects.project
        if project is None:
            self.setWindowTitle(APP_DISPLAY_NAME)
            return
        marker = " *" if project.dirty else ""
        self.setWindowTitle(f"{project.name}{marker} — {APP_DISPLAY_NAME}")

    def _refresh_recent_menu(self) -> None:
        self.recent_menu.clear()
        for path in self.ctx.settings.recent_projects[:8]:
            action = QAction(path, self.recent_menu)
            action.triggered.connect(lambda _, p=path: self._open_project_path(Path(p)))
            self.recent_menu.addAction(action)
        if self.recent_menu.isEmpty():
            action = QAction("（暂无）", self.recent_menu)
            action.setEnabled(False)
            self.recent_menu.addAction(action)

    def _on_tree_activated(self, kind: str, key: str) -> None:
        project = self.ctx.projects.project
        if project is None:
            return
        if kind == "boreholes":
            self.data_table.show_boreholes(project.drillholes)
            self.tabs.setCurrentWidget(self.data_table)
        elif kind == "faults":
            self.data_table.show_faults(project.faults)
            self.tabs.setCurrentWidget(self.data_table)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 协议
        project = self.ctx.projects.project
        if project is not None and project.dirty:
            choice = QMessageBox.question(
                self,
                "保存工程",
                "工程已修改，是否保存后退出？",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if choice == QMessageBox.StandardButton.Save:
                if not self._save_project():
                    event.ignore()
                    return
        self.ctx.shutdown()
        event.accept()


def _action(text: str, shortcut: str, slot) -> QAction:
    action = QAction(text, None)
    if shortcut:
        action.setShortcut(QKeySequence(shortcut))
    action.triggered.connect(lambda: slot())
    return action


def _dock(title: str, widget: QWidget) -> QDockWidget:
    dock = QDockWidget(title)
    dock.setObjectName(f"dock_{title}")
    dock.setWidget(widget)
    dock.setAllowedAreas(
        Qt.DockWidgetArea.LeftDockWidgetArea
        | Qt.DockWidgetArea.RightDockWidgetArea
        | Qt.DockWidgetArea.BottomDockWidgetArea
    )
    return dock


__all__ = ["MainWindow"]
