"""高级模型修复对话框（M6）：空洞、层序、简化、光顺等选项。"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from geomodel3d.algorithms.repair import RepairOptions, RepairReport
from geomodel3d.ui.async_runner import AsyncRunner


class RepairDialog(QDialog):
    """模型修复选项与执行。"""

    def __init__(self, context, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = context
        self.report: Optional[RepairReport] = None
        self.setWindowTitle("高级模型修复")
        self.resize(520, 480)
        self._runner = AsyncRunner(context.tasks, self) if context.tasks is not None else None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        group = QGroupBox("修复选项")
        form = QFormLayout(group)

        self._fill_holes = QCheckBox("填充内部空洞")
        self._fill_holes.setChecked(True)
        form.addRow(self._fill_holes)

        self._fix_layer_order = QCheckBox("修正层序倒置 / 超覆")
        form.addRow(self._fix_layer_order)

        self._merge_layers = QCheckBox("合并同层单元（体积守恒）")
        form.addRow(self._merge_layers)

        self._weld = QCheckBox("焊接重合顶点")
        self._weld.setChecked(True)
        form.addRow(self._weld)

        self._remove_degenerate = QCheckBox("剔除退化单元")
        self._remove_degenerate.setChecked(True)
        form.addRow(self._remove_degenerate)

        self._remove_duplicate = QCheckBox("剔除重复单元")
        self._remove_duplicate.setChecked(True)
        form.addRow(self._remove_duplicate)

        self._fix_normals = QCheckBox("修正法向一致性")
        self._fix_normals.setChecked(True)
        form.addRow(self._fix_normals)

        self._simplify = QDoubleSpinBox()
        self._simplify.setRange(0.0, 1_000_000.0)
        self._simplify.setDecimals(3)
        self._simplify.setSingleStep(0.1)
        self._simplify.setSuffix(" m")
        self._simplify.setToolTip("0 表示不简化；大于 0 时按容差聚类顶点。")
        form.addRow("简化容差", self._simplify)

        self._smooth = QSpinBox()
        self._smooth.setRange(0, 20)
        self._smooth.setSuffix(" 次")
        form.addRow("光顺次数", self._smooth)
        layout.addWidget(group)

        buttons = QHBoxLayout()
        self._run_btn = QPushButton("执行修复")
        self._run_btn.setProperty("accent", True)
        self._run_btn.clicked.connect(self._run)
        buttons.addWidget(self._run_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._status = QLabel("选择修复选项后执行")
        self._status.setProperty("role", "hint")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def _options(self) -> RepairOptions:
        return RepairOptions(
            weld=self._weld.isChecked(),
            simplify_tolerance=self._simplify.value(),
            merge_layers=self._merge_layers.isChecked(),
            fill_holes=self._fill_holes.isChecked(),
            fix_layer_order=self._fix_layer_order.isChecked(),
            remove_degenerate=self._remove_degenerate.isChecked(),
            remove_duplicate=self._remove_duplicate.isChecked(),
            fix_normals=self._fix_normals.isChecked(),
            smooth_iterations=self._smooth.value(),
        )

    def _run(self) -> None:
        options = self._options()
        if self._runner is not None:
            self._run_btn.setEnabled(False)
            self._status.setProperty("role", "hint")
            self._status.setText("正在后台修复模型…")
            self._runner.submit(
                lambda ctx: self.ctx.repair.repair(options),
                name="repair-advanced",
                on_finished=self._finish,
                on_failed=self._fail,
            )
            return
        try:
            report = self.ctx.repair.repair(options)
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            return
        self._finish(report)

    def _finish(self, report: RepairReport) -> None:
        self.report = report
        self._run_btn.setEnabled(True)
        self._status.setProperty("role", "success")
        self._status.setText(report.summary())

    def _fail(self, exc: BaseException) -> None:
        self.report = None
        self._run_btn.setEnabled(True)
        error = self.ctx.exceptions.report(exc)
        self._status.setProperty("role", "danger")
        self._status.setText(f"修复失败：{error.message}")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 协议
        if self._runner is not None:
            self._runner.detach()
        super().closeEvent(event)


__all__ = ["RepairDialog"]
