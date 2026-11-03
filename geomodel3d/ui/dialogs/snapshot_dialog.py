"""版本历史对话框（M9-F3）：查看、创建、回滚与对比快照。"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class SnapshotDialog(QDialog):
    """版本快照时间轴。"""

    def __init__(self, context, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = context
        self.setWindowTitle("版本历史")
        self.resize(760, 460)
        self._build_ui()
        self._reload()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("选中一行可回滚；选中两行可对比。"))

        self._table = QTableWidget()
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self._table)

        buttons = QHBoxLayout()
        create_btn = QPushButton("创建快照")
        create_btn.setProperty("accent", True)
        create_btn.clicked.connect(self._create)
        restore_btn = QPushButton("回滚")
        restore_btn.clicked.connect(self._restore)
        diff_btn = QPushButton("对比所选")
        diff_btn.clicked.connect(self._diff)
        buttons.addWidget(create_btn)
        buttons.addWidget(restore_btn)
        buttons.addWidget(diff_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._status = QLabel("")
        self._status.setProperty("role", "hint")
        layout.addWidget(self._status)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def _reload(self) -> None:
        snapshots = self.ctx.projects.list_snapshots()
        headers = ["时间", "标签", "类型", "钻孔", "断层", "单元", "模型哈希"]
        self._table.clear()
        self._table.setColumnCount(len(headers))
        self._table.setRowCount(len(snapshots))
        self._table.setHorizontalHeaderLabels(headers)
        for row, item in enumerate(reversed(snapshots)):
            values = [
                item.get("created", "")[:19].replace("T", " "),
                item.get("label", ""),
                item.get("kind", ""),
                str(item.get("drillholes", 0)),
                str(item.get("faults", 0)),
                str(item.get("cells", 0)),
                item.get("mesh_hash", ""),
            ]
            for column, value in enumerate(values):
                self._table.setItem(row, column, QTableWidgetItem(value))
        self._ids = [item["id"] for item in reversed(snapshots)]
        self._table.resizeColumnsToContents()
        self._status.setText(f"共 {len(snapshots)} 个版本")

    def _selected_ids(self):
        rows = sorted({index.row() for index in self._table.selectedIndexes()})
        return [self._ids[row] for row in rows if 0 <= row < len(self._ids)]

    def _create(self) -> None:
        self.ctx.projects.snapshot("手动快照", kind="manual")
        self._reload()

    def _restore(self) -> None:
        selected = self._selected_ids()
        if len(selected) != 1:
            self._status.setText("请选择 1 行进行回滚")
            return
        if self.ctx.projects.restore(selected[0]):
            self._reload()
            self._status.setText("已回滚到所选版本")
        else:
            self._status.setText("回滚失败：版本不存在")

    def _diff(self) -> None:
        selected = self._selected_ids()
        if len(selected) != 2:
            self._status.setText("请选择 2 行进行对比")
            return
        result = self.ctx.projects.diff(selected[0], selected[1])
        changed = result["changed"]
        if not changed:
            self._status.setText("两个版本无差异")
            return
        lines = [f"{key}: {value[0]} → {value[1]}" for key, value in changed.items()]
        QMessageBox.information(self, "版本对比", "\n".join(lines))
