"""数据表格面板（7.3 中央 Tab）：钻孔 / 断层属性表。"""
from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from geomodel3d.domain.entities import Drillhole, Fault


class DataTablePanel(QTableWidget):
    """只读属性表（可扩展为可编辑 Model/View）。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)

    # ------------------------------------------------------------------ #
    def show_boreholes(self, holes: List[Drillhole]) -> None:
        headers = ["孔号", "X", "Y", "孔口高程", "孔深", "层数"]
        self._reset(headers, len(holes))
        for row, hole in enumerate(holes):
            values = [
                hole.hole_id,
                f"{hole.x:.2f}",
                f"{hole.y:.2f}",
                f"{hole.collar_elev:.2f}",
                f"{hole.total_depth:.2f}",
                str(len(hole.intervals)),
            ]
            self._set_row(row, values)

    def show_faults(self, faults: List[Fault]) -> None:
        headers = ["断层编号", "名称", "延伸长度", "影响深度", "倾角", "走向", "垂直落差", "状态"]
        self._reset(headers, len(faults))
        for row, fault in enumerate(faults):
            params = fault.params
            values = [
                fault.fault_id,
                fault.name,
                f"{params.length:.1f}",
                f"{params.depth_of_influence:.1f}",
                f"{params.dip:.1f}",
                f"{params.strike:.1f}",
                f"{params.throw_v:.1f}",
                fault.status,
            ]
            self._set_row(row, values)

    # ------------------------------------------------------------------ #
    def _reset(self, headers: List[str], rows: int) -> None:
        self.clear()
        self.setColumnCount(len(headers))
        self.setRowCount(rows)
        self.setHorizontalHeaderLabels(headers)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

    def _set_row(self, row: int, values: List[str]) -> None:
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setItem(row, column, item)


__all__ = ["DataTablePanel"]
