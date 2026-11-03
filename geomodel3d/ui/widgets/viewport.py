"""三维视口（M3 / 7.3 中央区）。

内嵌 PyVista QtInteractor，按地层分组着色显示 UMesh；支持主题背景、复位、
图层显隐、拾取查询、测量与剖切面。无模型时显示空态引导。
"""
from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from geomodel3d.app.theme import accent_color, viewport_background
from geomodel3d.config.schema import StratigraphyEntry
from geomodel3d.engine.umesh import UMesh

logger = logging.getLogger(__name__)


class ViewportWidget(QWidget):
    """三维视口控件。"""

    picked = pyqtSignal(dict)
    fullscreenRequested = pyqtSignal()
    popoutRequested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._theme = "light"
        self._mesh: Optional[UMesh] = None
        self._stratigraphy: List[StratigraphyEntry] = []
        self._actors: List[str] = []
        self._clip_enabled = False
        self._pick_enabled = False
        self._box_pick_enabled = False
        self._active_pick = "none"
        self._clip_fraction: Optional[float] = None
        self._clip_axis = "x"
        self._display_mode = "surface"
        self._z_scale = 0.0  # <=0 表示自动纵向放大
        self._display_scale = (1.0, 1.0, 0.0, 0.0)  # 经纬度数据的显示缩放
        self._display_bounds = None
        self._layer_actors: Dict[str, List[str]] = {}
        self._hidden_layers: set = set()
        self._drillholes: List = []
        self._faults: List = []
        self._show_boreholes = True
        self._show_faults = True
        self._pick_label: Dict[int, str] = {}
        self._pick_kind: Dict[int, str] = {}
        self._pick_id: Dict[str, int] = {}

        self._stack = QStackedLayout(self)
        self._empty = self._build_empty_state()
        self._stack.addWidget(self._empty)

        self._plotter = self._create_plotter()
        if self._plotter is not None:
            self._stack.addWidget(self._plotter.interactor)
            self._stack.setCurrentWidget(self._plotter.interactor)

        self._overlay = self._build_overlay()
        self._overlay.hide()

    # ------------------------------------------------------------------ #
    def _build_empty_state(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("尚未生成模型")
        title.setProperty("role", "title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel("导入钻孔/断层/地震数据后，点击“快速建模”开始")
        hint.setProperty("role", "hint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addWidget(hint)
        return widget

    def _build_overlay(self) -> QWidget:
        overlay = QWidget(self)
        overlay.setObjectName("viewportOverlay")
        overlay.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(overlay)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        self._fullscreen_btn = QToolButton(overlay)
        self._fullscreen_btn.setObjectName("viewportOverlayButton")
        self._fullscreen_btn.setAutoRaise(True)
        self._fullscreen_btn.setIconSize(QSize(18, 18))
        self._fullscreen_btn.setFixedSize(30, 30)
        self._fullscreen_btn.setToolTip("全屏显示（Esc 退出）")
        self._fullscreen_btn.clicked.connect(self.fullscreenRequested.emit)

        self._popout_btn = QToolButton(overlay)
        self._popout_btn.setObjectName("viewportOverlayButton")
        self._popout_btn.setAutoRaise(True)
        self._popout_btn.setIconSize(QSize(18, 18))
        self._popout_btn.setFixedSize(30, 30)
        self._popout_btn.setToolTip("浮窗显示三维视图")
        self._popout_btn.clicked.connect(self.popoutRequested.emit)

        layout.addWidget(self._fullscreen_btn)
        layout.addWidget(self._popout_btn)
        self._update_overlay_icons()
        return overlay

    def _update_overlay_icons(self) -> None:
        color = QColor(accent_color(self._theme))
        self._fullscreen_btn.setIcon(_make_fullscreen_icon(color))
        self._popout_btn.setIcon(_make_popout_icon(color))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 协议
        super().resizeEvent(event)
        self._position_overlay()

    def _position_overlay(self) -> None:
        if not hasattr(self, "_overlay") or not self._overlay.isVisible():
            return
        self._overlay.adjustSize()
        margin = 12
        self._overlay.move(
            max(0, self.width() - self._overlay.width() - margin),
            max(0, self.height() - self._overlay.height() - margin),
        )
        self._overlay.raise_()

    def _create_plotter(self):
        import os

        if os.environ.get("GEOMODEL3D_NO_VTK") or os.environ.get(
            "QT_QPA_PLATFORM", ""
        ).startswith("offscreen"):
            logger.info("检测到无 OpenGL 环境，跳过 VTK 视口初始化。")
            return None
        try:
            from pyvistaqt import QtInteractor
        except Exception as exc:  # noqa: BLE001
            logger.warning("PyVista QtInteractor 不可用: %s", exc)
            return None
        try:
            plotter = QtInteractor(self)
        except Exception as exc:  # noqa: BLE001
            logger.warning("三维视口初始化失败: %s", exc)
            return None
        plotter.set_background(viewport_background(self._theme))
        return plotter

    # ------------------------------------------------------------------ #
    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self._update_overlay_icons()
        if self._plotter is not None:
            self._plotter.set_background(viewport_background(theme))
            if self._mesh is not None:
                self._add_legend(
                    {
                        entry.layer_code: entry.color
                        for entry in self._stratigraphy
                    }
                )

    def display_mesh(
        self,
        mesh: UMesh,
        stratigraphy: List[StratigraphyEntry],
        drillholes: Optional[List] = None,
        faults: Optional[List] = None,
    ) -> None:
        """按地层分组显示统一模型，并叠加钻孔/断层参考数据。"""
        self._mesh = mesh
        self._stratigraphy = list(stratigraphy)
        if drillholes is not None:
            self._drillholes = list(drillholes)
        if faults is not None:
            self._faults = list(faults)
        self._clip_enabled = False
        self._hidden_layers = set()
        self._display_scale = display_xy_scale(mesh.bounds())
        if self._plotter is None:
            return
        self._render_layers()
        interactor = getattr(self._plotter, "interactor", None)
        if interactor is not None and self._stack.currentWidget() is not interactor:
            self._stack.setCurrentWidget(interactor)
        self._overlay.setVisible(True)
        self._position_overlay()

    def set_reference_data(self, drillholes: Optional[List] = None, faults: Optional[List] = None) -> None:
        """更新钻孔/断层参考数据；已有模型时立即重绘。"""
        if drillholes is not None:
            self._drillholes = list(drillholes)
        if faults is not None:
            self._faults = list(faults)
        if self._plotter is not None and self._mesh is not None:
            self._render_layers()

    def show_reference(self, *, boreholes: Optional[bool] = None, faults: Optional[bool] = None) -> None:
        """切换钻孔/断层参考数据显示。"""
        if boreholes is not None:
            self._show_boreholes = bool(boreholes)
        if faults is not None:
            self._show_faults = bool(faults)
        if self._plotter is not None and self._mesh is not None:
            self._render_layers()

    def _render_layers(self) -> None:
        if self._plotter is None or self._mesh is None:
            return
        self._plotter.clear()
        self._actors.clear()
        self._layer_actors = {}
        self._pick_label = {}
        self._pick_kind = {}
        self._pick_id = {}

        mesh = self._mesh
        grid = self._apply_display_scale(mesh.to_pyvista())
        colors = {entry.layer_code: entry.color for entry in self._stratigraphy}
        alphas = {entry.layer_code: entry.alpha for entry in self._stratigraphy}
        groups = mesh.face_groups or _groups_from_attr(mesh)

        if self._display_mode == "surface" and len(groups) > 1:
            if not self._render_layer_solids(colors, alphas):
                self._render_surfaces(grid, colors)
        else:
            self._render_volume(grid, groups, colors, alphas)

        self._render_reference(colors)
        self._plotter.show_bounds(
            color="#5A6B7C", use_3d_text=False, font_size=10,
            xtitle="X", ytitle="Y", ztitle="Z",
        )
        self._apply_scale()
        self._add_legend(colors)
        self._plotter.reset_camera()
        self._apply_layer_visibility()
        if self._active_pick == "cell":
            self.set_pick_enabled(True)
        elif self._active_pick == "box":
            self.set_box_pick_enabled(True)

    def _apply_layer_visibility(self) -> None:
        """恢复被工程树隐藏的图层（重渲染后不丢失显隐状态）。"""
        if self._plotter is None or not self._hidden_layers:
            return
        changed = False
        for code in self._hidden_layers:
            names = self._layer_actors.get(code) or [f"layer_{code}"]
            for name in names:
                actor = self._plotter.renderer.actors.get(name)
                if actor is not None:
                    actor.SetVisibility(False)
                    changed = True
        if changed:
            self._plotter.render()

    def _register_pick(self, label: str, kind: str) -> int:
        """为拾取注册数值 id（VTK 不支持非 ASCII 字符串数组，故用整数映射）。"""
        key = f"{kind}:{label}"
        existing = self._pick_id.get(key)
        if existing is not None:
            return existing
        index = len(self._pick_id)
        self._pick_id[key] = index
        self._pick_label[index] = str(label)
        self._pick_kind[index] = kind
        return index

    def _apply_display_scale(self, grid):
        """经纬度数据按米缩放 XY 便于显示（不影响模型/储量数据）。"""
        import numpy as np

        sx, sy, xc, yc = self._display_scale
        if sx == 1.0 and sy == 1.0 and xc == 0.0 and yc == 0.0:
            self._display_bounds = tuple(grid.bounds)
            return grid
        grid = grid.copy()
        points = np.asarray(grid.points, dtype=np.float64)
        points[:, 0] = (points[:, 0] - xc) * sx
        points[:, 1] = (points[:, 1] - yc) * sy
        grid.points = points
        self._display_bounds = tuple(grid.bounds)
        return grid

    def _render_volume(self, grid, groups, colors, alphas) -> None:
        if self._display_mode == "wireframe":
            style, edges = "wireframe", False
        elif self._display_mode == "mesh":
            style, edges = "surface", True
        else:
            style, edges = "surface", False
        if not groups:
            self._plotter.add_mesh(
                grid, color="#8FB0D0", style=style, show_edges=edges, name="model"
            )
            self._actors.append("model")
        clip = self._clip_plane()
        for code, indices in groups.items():
            if not indices:
                continue
            sub = grid.extract_cells(indices)
            if clip is not None:
                sub = sub.clip(normal=clip[0], origin=clip[1], crinkle=False)
                if sub.n_cells == 0:
                    continue
            sub.cell_data["layer"] = [int(self._register_pick(code, "layer"))] * sub.n_cells
            name = f"layer_{code}"
            self._plotter.add_mesh(
                sub,
                color=colors.get(code, "#8FB0D0"),
                opacity=alphas.get(code, 0.9),
                style=style,
                show_edges=edges,
                name=name,
                label=str(code),
            )
            self._actors.append(name)
            self._layer_actors.setdefault(code, []).append(name)

    def _render_layer_solids(self, colors, alphas) -> bool:
        """用建模保存的每层顶/底界面生成光滑封闭实体（对齐参考实现的层实体）。"""
        import numpy as np

        meta = self._mesh.meta if self._mesh is not None else {}
        data = meta.get("layer_surfaces") if isinstance(meta, dict) else None
        if not data:
            return False
        try:
            import pyvista as pv

            from geomodel3d.algorithms.surface_solid import layer_solid_geometry

            xs = np.asarray(data.get("x", []), dtype=np.float64)
            ys = np.asarray(data.get("y", []), dtype=np.float64)
            zs = np.asarray(data.get("z", []), dtype=np.float64)
            codes = list(data.get("codes", []))
            if xs.size < 2 or ys.size < 2 or zs.ndim != 3 or zs.shape[0] < len(codes) + 1:
                return False
        except Exception:  # noqa: BLE001
            logger.debug("层实体元数据不可用", exc_info=True)
            return False

        clip = self._clip_plane()
        sx, sy, xc, yc = self._display_scale
        gx = (xs - xc) * sx
        gy = (ys - yc) * sy
        drawn = 0
        for order, code in enumerate(codes):
            verts, faces = layer_solid_geometry(gx, gy, zs[order], zs[order + 1])
            if faces.size == 0:
                continue
            solid = pv.PolyData(verts, faces)
            if clip is not None:
                solid = solid.clip(normal=clip[0], origin=clip[1])
                if solid.n_cells == 0:
                    continue
            solid.cell_data["layer"] = [int(self._register_pick(code, "layer"))] * solid.n_cells
            name = f"layer_{code}"
            self._plotter.add_mesh(
                solid,
                color=colors.get(code, "#8FB0D0"),
                opacity=alphas.get(code, 0.9),
                show_edges=False,
                smooth_shading=True,
                name=name,
                label=str(code),
            )
            self._actors.append(name)
            self._layer_actors.setdefault(code, []).append(name)
            drawn += 1
        return drawn > 0

    def _render_surfaces(self, grid, colors) -> None:
        """从体元层场提取层界面等值面，渲染为连续曲面（地质外观）。"""
        import numpy as np

        ordered = sorted(self._stratigraphy, key=lambda entry: entry.order)
        position_to_code = {index: entry.layer_code for index, entry in enumerate(ordered)}
        try:
            point_grid = grid.cell_data_to_point_data()
        except Exception:  # noqa: BLE001
            point_grid = grid
        layers = sorted(int(v) for v in np.unique(np.asarray(self._mesh.cell_attr.get("layer", []))))
        drawn = 0
        for order in layers[:-1]:
            try:
                surface = point_grid.contour([order + 0.5], scalars="layer")
            except Exception:  # noqa: BLE001
                continue
            if surface.n_points == 0:
                continue
            code = position_to_code.get(order + 1, position_to_code.get(order, ""))
            name = f"surface_{order}"
            surface.cell_data["layer"] = [int(self._register_pick(code, "layer"))] * surface.n_cells
            self._plotter.add_mesh(
                surface,
                color=colors.get(code, "#8FB0D0"),
                opacity=1.0,
                smooth_shading=True,
                name=name,
                label=str(code),
            )
            self._actors.append(name)
            self._layer_actors.setdefault(code, []).append(name)
            drawn += 1
        # 半透明外表面，使模型看起来是封闭块体
        try:
            outer = grid.extract_surface()
            self._plotter.add_mesh(
                outer, color="#B8C4D0", opacity=0.12, show_edges=False, name="outer"
            )
            self._actors.append("outer")
        except Exception:  # noqa: BLE001
            pass
        if drawn == 0:
            self._render_volume(grid, self._mesh.face_groups or _groups_from_attr(self._mesh), colors, {})

    def _render_reference(self, colors) -> None:
        """叠加参考数据：钻孔（孔口点 + 分层轨迹）与断层迹线（M3-F1/F4）。"""
        try:
            if self._show_boreholes and self._drillholes:
                self._render_boreholes(colors)
            if self._show_faults and self._faults:
                self._render_faults()
        except Exception:  # noqa: BLE001 - 参考数据显示失败不应影响模型
            logger.debug("参考数据渲染失败", exc_info=True)

    def _render_boreholes(self, colors) -> None:
        import numpy as np

        sx, sy, xc, yc = self._display_scale
        collars = [
            [(hole.x - xc) * sx, (hole.y - yc) * sy, float(hole.collar_elev)]
            for hole in self._drillholes
        ]
        if collars:
            name = "borehole_collars"
            self._plotter.add_points(
                np.asarray(collars, dtype=np.float64),
                color="#D64550",
                point_size=9,
                render_points_as_spheres=True,
                name=name,
                label="钻孔",
            )
            self._actors.append(name)
            self._layer_actors.setdefault("__boreholes__", []).append(name)

        segments: Dict[str, list] = {}
        for hole in self._drillholes:
            x, y = (hole.x - xc) * sx, (hole.y - yc) * sy
            for interval in hole.intervals:
                z_top = hole.collar_elev - interval.depth_top
                z_bottom = hole.collar_elev - interval.depth_bottom
                segments.setdefault(interval.layer_code, []).append(
                    [[x, y, z_top], [x, y, z_bottom]]
                )
        for code, lines in segments.items():
            poly = _polyline([point for segment in lines for point in segment], per_line=2)
            if poly is None:
                continue
            name = f"bore_{code}"
            poly.cell_data["layer"] = [int(self._register_pick(code, "borehole"))] * poly.n_cells
            self._plotter.add_mesh(
                poly, color=colors.get(code, "#8FB0D0"), line_width=3,
                name=name, label=f"钻孔_{code}",
            )
            self._actors.append(name)
            self._layer_actors.setdefault("__boreholes__", []).append(name)

    def _render_faults(self) -> None:
        sx, sy, xc, yc = self._display_scale
        for fault in self._faults:
            trace = getattr(fault, "trace", None)
            if not trace or len(trace) < 2:
                continue
            points = [
                [(p[0] - xc) * sx, (p[1] - yc) * sy, float(p[2])] for p in trace
            ]
            poly = _polyline(points)
            if poly is None:
                continue
            name = f"fault_{fault.fault_id}"
            poly.cell_data["layer"] = [int(self._register_pick(fault.fault_id, "fault"))] * poly.n_cells
            self._plotter.add_mesh(
                poly, color="#E8913A", line_width=4, name=name,
                label=f"断层 {fault.fault_id}",
            )
            self._actors.append(name)
            self._layer_actors.setdefault("__faults__", []).append(name)

    def clear(self) -> None:
        self._mesh = None
        self._stratigraphy = []
        self._clip_enabled = False
        self._display_bounds = None
        self._display_scale = (1.0, 1.0, 0.0, 0.0)
        self._hidden_layers = set()
        self._drillholes = []
        self._faults = []
        self._active_pick = "none"
        self._pick_enabled = False
        self._box_pick_enabled = False
        if self._plotter is not None:
            self._plotter.clear()
            self._actors.clear()
            self._layer_actors = {}
            self._pick_label = {}
            self._pick_kind = {}
            self._pick_id = {}
        self._overlay.hide()
        self._stack.setCurrentWidget(self._empty)

    def reset_view(self) -> None:
        if self._plotter is not None:
            self._plotter.reset_camera()

    def refresh(self) -> None:
        """重新定位悬浮工具并刷新 VTK 画面（窗口重挂载后调用）。"""
        self._position_overlay()
        if self._plotter is not None:
            try:
                self._plotter.render()
            except Exception:  # noqa: BLE001
                logger.debug("视图刷新失败", exc_info=True)

    def shutdown(self) -> None:
        """释放独立视口的 VTK 资源（浮窗关闭时调用）。"""
        if self._plotter is None:
            return
        try:
            self._plotter.close()
        except Exception:  # noqa: BLE001
            logger.debug("关闭 VTK 视口失败", exc_info=True)
        deep_clean = getattr(self._plotter, "deep_clean", None)
        if callable(deep_clean):
            try:
                deep_clean()
            except Exception:  # noqa: BLE001
                logger.debug("清理 VTK 视口失败", exc_info=True)

    def set_display_mode(self, mode: str) -> None:
        """设置显示模式：surface / volume（实体）/ wireframe（线框）/ mesh（网格）。"""
        if mode not in {"surface", "volume", "wireframe", "mesh"}:
            mode = "surface"
        self._display_mode = mode
        if self._plotter is not None and self._mesh is not None:
            self._render_layers()

    def _add_legend(self, colors) -> None:
        """叠加地层图例浮层（M3-F1）。"""
        if self._plotter is None:
            return
        entries = [
            (entry.layer_code, colors.get(entry.layer_code, "#8FB0D0"))
            for entry in sorted(self._stratigraphy, key=lambda item: item.order)
        ]
        if not entries:
            return
        try:
            light = self._theme == "light"
            background = (1.0, 1.0, 1.0) if light else (0.12, 0.15, 0.19)
            text_color = (0.17, 0.20, 0.25) if light else (0.89, 0.91, 0.94)
            height = min(0.30, max(0.12, 0.045 * len(entries) + 0.05))
            legend = self._plotter.add_legend(
                entries,
                bcolor=background,
                border=True,
                size=(0.12, height),
                loc="upper right",
                name="layer_legend",
                background_opacity=0.80,
            )
            if legend is not None:
                prop = legend.GetEntryTextProperty()
                prop.SetFontSize(9)
                prop.SetColor(*text_color)
        except Exception:  # noqa: BLE001 - 图例不可用不应影响显示
            logger.debug("图例叠加不可用", exc_info=True)

    def set_vertical_exaggeration(self, factor: float) -> None:
        """设置纵向放大倍数（``<=0`` 为自动，按水平/垂向跨度自动取值）。"""
        self._z_scale = float(factor)
        if self._plotter is None or self._mesh is None:
            return
        if self._clip_enabled:
            self._apply_scale()
            self._plotter.render()
        else:
            self._render_layers()

    @property
    def vertical_exaggeration(self) -> float:
        """当前生效的纵向放大倍数。"""
        if self._mesh is None:
            return 1.0
        if self._z_scale > 0:
            return float(self._z_scale)
        bounds = self._display_bounds or self._mesh.bounds()
        return auto_vertical_exaggeration(bounds)

    def _apply_scale(self) -> None:
        if self._plotter is None or self._mesh is None:
            return
        try:
            self._plotter.set_scale(
                xscale=1.0, yscale=1.0, zscale=self.vertical_exaggeration, reset_camera=False
            )
        except Exception:  # noqa: BLE001 - 放大设置失败不应影响显示
            logger.debug("纵向放大设置失败", exc_info=True)

    def set_layer_visible(self, code: str, visible: bool) -> None:
        """按地层代码切换图层显隐（对应工程树勾选，兼容表面/实体/线框模式）。"""
        if visible:
            self._hidden_layers.discard(code)
        else:
            self._hidden_layers.add(code)
        if self._plotter is None:
            return
        names = list(self._layer_actors.get(code, []))
        if not names:
            names = [f"layer_{code}"]
        changed = False
        for name in names:
            actor = self._plotter.renderer.actors.get(name)
            if actor is not None:
                actor.SetVisibility(bool(visible))
                changed = True
        if changed:
            self._plotter.render()

    # ------------------------------------------------------------------ #
    # 拾取 / 测量 / 剖切
    # ------------------------------------------------------------------ #
    def set_pick_enabled(self, enabled: bool) -> None:
        """开启/关闭单元拾取查询。"""
        self._pick_enabled = enabled
        if self._plotter is None:
            return
        if enabled:
            self._box_pick_enabled = False
            self._active_pick = "cell"
            try:
                self._plotter.enable_cell_picking(
                    callback=self._on_pick, show_message=False, show=False, through=False
                )
            except Exception:  # noqa: BLE001 - 拾取不可用时回退且不报错
                logger.warning("单元拾取不可用", exc_info=True)
                self._active_pick = "none"
                self._pick_enabled = False
        elif self._active_pick == "cell":
            self._active_pick = "none"
            try:
                self._plotter.disable_picking()
            except Exception:  # noqa: BLE001
                logger.debug("禁用拾取失败", exc_info=True)

    def _on_pick(self, picked) -> None:
        if picked is None:
            return
        try:
            import numpy as np

            n_cells = int(getattr(picked, "n_cells", 0))
            points = getattr(picked, "points", None)
            if n_cells:
                center = picked.cell_centers().points.mean(axis=0)
            elif points is not None and len(points):
                center = np.asarray(points, dtype=np.float64).mean(axis=0)
            else:
                return
            layer = None
            kind = None
            data = getattr(picked, "cell_data", None)
            if data is not None and "layer" in data and len(data["layer"]):
                pid = int(data["layer"][0])
                layer = self._pick_label.get(pid)
                kind = self._pick_kind.get(pid)
            self.picked.emit(
                {
                    "x": float(center[0]),
                    "y": float(center[1]),
                    "z": float(center[2]),
                    "layer": layer,
                    "kind": kind,
                    "cells": n_cells,
                }
            )
        except Exception:  # noqa: BLE001 - 拾取失败不影响交互
            logger.debug("拾取解析失败", exc_info=True)

    def add_measurement(self) -> None:
        """添加交互式测量控件（距离）；重复触发时先清除旧控件。"""
        if self._plotter is None:
            return
        try:
            if hasattr(self._plotter, "clear_measure_widgets"):
                self._plotter.clear_measure_widgets()
            self._plotter.add_measurement_widget()
        except Exception:  # noqa: BLE001
            logger.debug("测量控件不可用", exc_info=True)

    def set_clip_enabled(self, enabled: bool) -> None:
        """开启/关闭交互式剖切面（按 layer 着色）。"""
        self._clip_enabled = enabled
        if self._plotter is None or self._mesh is None:
            return
        if not enabled:
            self._render_layers()
            return
        self._plotter.clear()
        self._actors.clear()
        self._layer_actors = {}
        grid = self._apply_display_scale(self._mesh.to_pyvista())
        try:
            self._plotter.add_mesh_clip_plane(
                grid, scalars="layer", cmap="tab20", show_edges=False, name="clip"
            )
            self._plotter.show_bounds(
                color="#5A6B7C", use_3d_text=False, font_size=10,
                xtitle="X", ytitle="Y", ztitle="Z",
            )
            self._apply_scale()
            self._plotter.reset_camera()
        except Exception:  # noqa: BLE001
            logger.warning("剖切面不可用，回退普通显示", exc_info=True)
            self._clip_enabled = False
            self._render_layers()

    def set_clip_fraction(self, axis: str, fraction: float) -> None:
        """按坐标轴与比例设置剖切位置（0 不剖切，1 剖切到末端）。"""
        self._clip_axis = axis.lower()
        self._clip_fraction = None if fraction <= 0.0 else min(float(fraction), 1.0)
        if self._plotter is not None and self._mesh is not None and not self._clip_enabled:
            self._render_layers()

    def _clip_plane(self):
        if self._mesh is None or self._clip_fraction is None:
            return None
        bounds = self._display_bounds or self._mesh.bounds()
        return clip_plane_from_bounds(bounds, self._clip_axis, self._clip_fraction)

    def set_box_pick_enabled(self, enabled: bool) -> None:
        """开启/关闭框选查询（矩形选择）。"""
        self._box_pick_enabled = enabled
        if self._plotter is None:
            return
        if enabled:
            self._pick_enabled = False
            self._active_pick = "box"
            try:
                self._plotter.enable_rectangle_picking(
                    callback=self._on_pick, show_message=False
                )
            except Exception:  # noqa: BLE001
                logger.warning("框选拾取不可用", exc_info=True)
                self._active_pick = "none"
                self._box_pick_enabled = False
        elif self._active_pick == "box":
            self._active_pick = "none"
            try:
                self._plotter.disable_picking()
            except Exception:  # noqa: BLE001
                logger.debug("禁用框选失败", exc_info=True)

    def screenshot(self, path: str) -> None:
        if self._plotter is not None:
            self._plotter.screenshot(path)

    @property
    def plotter(self):
        return self._plotter


def format_pick_info(info: Dict[str, object]) -> str:
    """把拾取结果格式化为状态栏文本。"""
    layer = info.get("layer")
    kind = info.get("kind")
    if kind == "borehole":
        layer_text = f"钻孔·地层{layer}"
    elif kind == "fault":
        layer_text = f"断层 {layer}"
    elif layer is not None:
        layer_text = f"地层#{layer}"
    else:
        layer_text = "地层未知"
    return (
        f"拾取：{layer_text} · "
        f"X={float(info.get('x', 0)):.1f} Y={float(info.get('y', 0)):.1f} Z={float(info.get('z', 0)):.1f} · "
        f"单元 {int(info.get('cells', 0))}"
    )


def _polyline(points, per_line: int = 0):
    """由点列表构造 PolyData 线；``per_line>0`` 表示每若干点组成一段独立线段。"""
    try:
        import numpy as np
        import pyvista as pv
    except Exception:  # noqa: BLE001
        return None
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[0] < 2:
        return None
    if per_line and per_line >= 2:
        count = (pts.shape[0] // per_line) * per_line
        cells: List[int] = []
        for start in range(0, count, per_line):
            cells.append(per_line)
            cells.extend(range(start, start + per_line))
        lines = np.asarray(cells, dtype=np.int64)
    else:
        lines = np.concatenate([[pts.shape[0]], np.arange(pts.shape[0])]).astype(np.int64)
    try:
        return pv.PolyData(pts, lines=lines)
    except Exception:  # noqa: BLE001
        return None


def display_xy_scale(bounds):
    """经纬度数据返回 (x_scale, y_scale, x_center, y_center)（米/度）；投影坐标为恒等。"""
    xmin, xmax, ymin, ymax = bounds[0], bounds[1], bounds[2], bounds[3]
    if (
        abs(xmin) <= 180 and abs(xmax) <= 180
        and abs(ymin) <= 90 and abs(ymax) <= 90
        and max(abs(xmax - xmin), abs(ymax - ymin)) > 1e-6
    ):
        latitude = (ymin + ymax) / 2.0
        return (
            111320.0 * math.cos(math.radians(latitude)),
            111320.0,
            (xmin + xmax) / 2.0,
            (ymin + ymax) / 2.0,
        )
    return (1.0, 1.0, 0.0, 0.0)


def auto_vertical_exaggeration(bounds, *, target_ratio: float = 0.3, max_factor: float = 500.0) -> float:
    """按包围盒自动计算纵向放大倍数，避免大面积薄层模型显示过扁。

    与参考实现一致：目标垂向约为水平跨度 30%，上限 500×；高大模型不缩小。
    """
    x_span = abs(bounds[1] - bounds[0])
    y_span = abs(bounds[3] - bounds[2])
    z_span = abs(bounds[5] - bounds[4])
    horizontal = max(x_span, y_span, 1e-9)
    vertical = max(z_span, 1e-9)
    return float(min(max(target_ratio * horizontal / vertical, 1.0), max_factor))


def clip_plane_from_bounds(bounds, axis: str, fraction: float):
    """由包围盒与轴/比例计算剖切平面 ``(normal, origin)``。

    ``bounds`` 为 VTK 顺序 ``[xmin,xmax,ymin,ymax,zmin,zmax]``。
    """
    import numpy as np

    lo = np.array([bounds[0], bounds[2], bounds[4]], dtype=float)
    hi = np.array([bounds[1], bounds[3], bounds[5]], dtype=float)
    axis = axis.lower()
    normal = np.zeros(3)
    origin = lo.copy()
    if axis == "y":
        normal[1] = 1.0
        origin[1] = lo[1] + fraction * (hi[1] - lo[1])
    elif axis == "z":
        normal[2] = 1.0
        origin[2] = lo[2] + fraction * (hi[2] - lo[2])
    else:
        normal[0] = 1.0
        origin[0] = lo[0] + fraction * (hi[0] - lo[0])
    return normal, origin


def _groups_from_attr(mesh: UMesh) -> Dict[str, List[int]]:
    if "layer" not in mesh.cell_attr:
        return {}
    layers = mesh.cell_attr["layer"]
    groups: Dict[str, List[int]] = {}
    for code in sorted(set(int(v) for v in layers)):
        groups[str(code)] = [i for i, value in enumerate(layers) if int(value) == code]
    return groups


def _make_fullscreen_icon(color: QColor) -> QIcon:
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color, 2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    # 四个角的外扩括号
    painter.drawLine(4, 9, 4, 4)
    painter.drawLine(4, 4, 9, 4)
    painter.drawLine(15, 4, 20, 4)
    painter.drawLine(20, 4, 20, 9)
    painter.drawLine(20, 15, 20, 20)
    painter.drawLine(20, 20, 15, 20)
    painter.drawLine(9, 20, 4, 20)
    painter.drawLine(4, 20, 4, 15)
    painter.end()
    return QIcon(pixmap)


def _make_popout_icon(color: QColor) -> QIcon:
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color, 2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawRoundedRect(8, 3, 11, 11, 2, 2)
    painter.drawRoundedRect(3, 8, 11, 11, 2, 2)
    painter.end()
    return QIcon(pixmap)


__all__ = ["ViewportWidget", "format_pick_info", "auto_vertical_exaggeration", "display_xy_scale"]
