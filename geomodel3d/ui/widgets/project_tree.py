"""工程树 / 图层面板（7.3 左 Dock）。

以树形展示工程结构：数据源、地层字典、模型与方案版本；
支持勾选显隐（图层）与右键上下文菜单。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor
from PyQt6.QtWidgets import QMenu, QTreeWidget, QTreeWidgetItem, QWidget

from geomodel3d.domain.project import Project

ROLE_KIND = Qt.ItemDataRole.UserRole
ROLE_KEY = Qt.ItemDataRole.UserRole + 1


class ProjectTree(QTreeWidget):
    """工程结构树。"""

    visibilityChanged = pyqtSignal(str, bool)
    itemActivated = pyqtSignal(str, str)
    refreshRequested = pyqtSignal()
    exportRequested = pyqtSignal()
    zoomRequested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setAlternatingRowColors(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.itemChanged.connect(self._on_item_changed)
        self.itemDoubleClicked.connect(self._on_double_click)
        self._loading = False

    # ------------------------------------------------------------------ #
    def refresh(self, project: Optional[Project]) -> None:
        self._loading = True
        self.clear()
        if project is None:
            self._loading = False
            return

        root = QTreeWidgetItem([f"工程：{project.name}"])
        root.setData(0, ROLE_KIND, "project")
        self.addTopLevelItem(root)

        data_node = _child(root, "数据源", "folder")
        for text, kind, key in (
            (f"钻孔 ({len(project.drillholes)})", "boreholes", "__boreholes__"),
            (f"断层 ({len(project.faults)})", "faults", "__faults__"),
            (f"地震趋势面 ({len(project.seismic)})", "seismic", "__seismic__"),
        ):
            item = _leaf(data_node, text, kind, key)
            item.setCheckState(0, Qt.CheckState.Checked)

        strat_node = _child(root, "地层字典", "folder")
        for entry in sorted(project.config.stratigraphy, key=lambda e: e.order):
            item = _leaf(strat_node, f"{entry.layer_code} {entry.name}".strip(), "layer", entry.layer_code)
            item.setCheckState(0, Qt.CheckState.Checked)
            item.setForeground(0, QColor(entry.color))

        model_node = _child(root, "模型", "folder")
        if project.has_model():
            _leaf(model_node, f"统一模型 ({project.mesh.n_cells} 单元)", "mesh")
        else:
            _leaf(model_node, "统一模型（未生成）", "mesh-empty")

        runs_node = _child(root, "方案版本", "folder")
        for run in project.runs[-10:]:
            _leaf(runs_node, f"{run.run_id} · {run.engine.value} · {run.status.value}", "run", run.run_id)

        root.setExpanded(True)
        data_node.setExpanded(True)
        strat_node.setExpanded(True)
        model_node.setExpanded(True)
        self._loading = False

    # ------------------------------------------------------------------ #
    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._loading:
            return
        kind = item.data(0, ROLE_KIND)
        checked = item.checkState(0) == Qt.CheckState.Checked
        if kind == "layer":
            self.visibilityChanged.emit(item.data(0, ROLE_KEY) or "", checked)
        elif kind in ("boreholes", "faults", "seismic"):
            self.visibilityChanged.emit(item.data(0, ROLE_KEY) or kind, checked)

    def _on_double_click(self, item: QTreeWidgetItem, column: int) -> None:
        kind = item.data(0, ROLE_KIND) or ""
        key = item.data(0, ROLE_KEY) or ""
        self.itemActivated.emit(kind, key)

    def _show_menu(self, position) -> None:
        item = self.itemAt(position)
        menu = QMenu(self)
        if item is not None:
            kind = item.data(0, ROLE_KIND)
            key = item.data(0, ROLE_KEY) or ""
            if kind == "faults":
                menu.addAction(_menu_action("编辑断层参数", menu, lambda: self.itemActivated.emit("faults", key)))
            if kind in ("boreholes", "faults", "seismic"):
                menu.addAction(_menu_action("属性表", menu, lambda: self.itemActivated.emit(kind, key)))
            if kind == "mesh":
                menu.addAction(_menu_action("缩放到模型", menu, self.zoomRequested.emit))
            menu.addAction(_menu_action("导出…", menu, self.exportRequested.emit))
        menu.addAction(_menu_action("刷新", menu, self.refreshRequested.emit))
        menu.exec(self.viewport().mapToGlobal(position))
        menu.deleteLater()


def _menu_action(text: str, parent: QWidget, slot) -> QAction:
    action = QAction(text, parent)
    action.triggered.connect(lambda: slot())
    return action


def _child(parent: QTreeWidgetItem, text: str, kind: str) -> QTreeWidgetItem:
    item = QTreeWidgetItem([text])
    item.setData(0, ROLE_KIND, kind)
    parent.addChild(item)
    return item


def _leaf(parent: QTreeWidgetItem, text: str, kind: str, key: str = "") -> QTreeWidgetItem:
    item = QTreeWidgetItem([text])
    item.setData(0, ROLE_KIND, kind)
    item.setData(0, ROLE_KEY, key)
    parent.addChild(item)
    return item


__all__ = ["ProjectTree"]
