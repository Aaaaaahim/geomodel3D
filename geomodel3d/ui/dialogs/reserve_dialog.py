"""储量计算对话框（M4 / 5.4）。"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from geomodel3d.domain.reserve import ReserveMetrics, ReserveResult
from geomodel3d.ui.async_runner import AsyncRunner


class ReserveDialog(QDialog):
    """储量方案配置、计算与报告导出。"""

    def __init__(self, context, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = context
        self._result: Optional[ReserveResult] = None
        self.setWindowTitle("储量计算")
        self.resize(720, 620)
        self._runner = AsyncRunner(context.tasks, self) if context.tasks is not None else None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        project = self.ctx.projects.project
        default_layers = ",".join(entry.layer_code for entry in project.config.stratigraphy[-1:]) if project else ""

        form = QFormLayout()
        self._mineral = QLineEdit("coal")
        form.addRow("矿种", self._mineral)
        self._layers = QLineEdit(default_layers)
        self._layers.setPlaceholderText("矿体地层代码，逗号分隔")
        form.addRow("矿体地层", self._layers)
        self._method = QComboBox()
        self._method.addItems(["体积法", "块段法"])
        form.addRow("计算方法", self._method)
        self._density = _spin(1.0, 5.0, 2.6, 0.05)
        form.addRow("矿石体重 (t/m³)", self._density)
        self._cutoff = _spin(0.0, 100.0, project.config.reserve.cutoff_grade if project else 0.5, 0.1)
        form.addRow("边界品位", self._cutoff)
        self._min_thickness = _spin(0.0, 50.0, project.config.reserve.min_thickness if project else 1.0, 0.1)
        form.addRow("最小可采厚度 (m)", self._min_thickness)
        self._measured = _spin(10.0, 2000.0, 150.0, 10.0)
        form.addRow("探明间距 (m)", self._measured)
        self._indicated = _spin(10.0, 5000.0, 300.0, 10.0)
        form.addRow("控制间距 (m)", self._indicated)
        self._block = QLineEdit("50,50,25")
        form.addRow("块段尺寸 (m)", self._block)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self._run_btn = QPushButton("计算")
        self._run_btn.setProperty("accent", True)
        self._run_btn.clicked.connect(self._run)
        self._export_btn = QPushButton("导出报告…")
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export)
        self._sensitivity_btn = QPushButton("灵敏度试算")
        self._sensitivity_btn.setEnabled(False)
        self._sensitivity_btn.clicked.connect(self._sensitivity)
        buttons.addWidget(self._run_btn)
        buttons.addWidget(self._sensitivity_btn)
        buttons.addWidget(self._export_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._summary = QLabel("尚未计算")
        self._summary.setProperty("role", "hint")
        layout.addWidget(self._summary)

        self._table = QTableWidget()
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    # ------------------------------------------------------------------ #
    def _metrics(self) -> ReserveMetrics:
        layers = [code.strip() for code in self._layers.text().split(",") if code.strip()]
        if not layers:
            raise ValueError("请填写矿体地层代码")
        try:
            block = [float(v) for v in self._block.text().split(",") if v.strip()]
        except ValueError as exc:
            raise ValueError("块段尺寸必须为逗号分隔的数字，如 50,50,25") from exc
        if len(block) != 3:
            raise ValueError("块段尺寸需要 3 个正数（dx,dy,dz）")
        return ReserveMetrics(
            mineral=self._mineral.text().strip() or "ore",
            body_layers=layers,
            method="block" if self._method.currentIndex() == 1 else "volume",
            density=self._density.value(),
            cutoff_grade=self._cutoff.value(),
            min_thickness=self._min_thickness.value(),
            measured_distance=self._measured.value(),
            indicated_distance=self._indicated.value(),
            block_size=block,
        )

    def _run(self) -> None:
        try:
            metrics = self._metrics()
        except Exception as exc:  # noqa: BLE001
            self._fail_run(exc)
            return

        if self._runner is not None:
            self._run_btn.setEnabled(False)
            self._summary.setProperty("role", "hint")
            self._summary.setText("正在后台计算储量…")
            self._runner.submit(
                lambda ctx: self.ctx.reserve.plan(metrics),
                name="reserve",
                on_finished=self._finish_run,
                on_failed=self._fail_run,
            )
            return
        try:
            result = self.ctx.reserve.plan(metrics)
        except Exception as exc:  # noqa: BLE001
            self._fail_run(exc)
            return
        self._finish_run(result)

    def _finish_run(self, result: ReserveResult) -> None:
        self._result = result
        self._run_btn.setEnabled(True)
        project = self.ctx.projects.project
        if project is not None:
            project.config.reserve = project.config.reserve.model_copy(
                update={"body_layers": list(result.metrics.body_layers)}
            )
        self._summary.setProperty("role", "success")
        self._summary.setText(result.summary_text())
        self._export_btn.setEnabled(True)
        self._sensitivity_btn.setEnabled(True)
        self._populate(result)

    def _fail_run(self, exc: BaseException) -> None:
        self._result = None
        self._run_btn.setEnabled(True)
        self._export_btn.setEnabled(False)
        self._sensitivity_btn.setEnabled(False)
        error = self.ctx.exceptions.report(exc)
        self._summary.setProperty("role", "danger")
        self._summary.setText(f"计算失败：{error.message}")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 协议
        if self._runner is not None:
            self._runner.detach()
        super().closeEvent(event)

    def _populate(self, result: ReserveResult) -> None:
        headers = ["分级", "块段数", "体积(m³)", "矿石量(t)", "金属量", "平均品位", "平均厚度(m)"]
        self._table.clear()
        self._table.setColumnCount(len(headers))
        self._table.setRowCount(len(result.by_class))
        self._table.setHorizontalHeaderLabels(headers)
        for row, (key, summary) in enumerate(sorted(result.by_class.items())):
            values = [
                key,
                str(summary.block_count),
                f"{summary.volume:,.0f}",
                f"{summary.tonnage:,.0f}",
                f"{summary.metal:,.2f}",
                f"{summary.avg_grade:.4f}",
                f"{summary.avg_thickness:.2f}",
            ]
            for column, value in enumerate(values):
                self._table.setItem(row, column, QTableWidgetItem(value))

    def _export(self) -> None:
        if self._result is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出储量报告", "reserve_report.xlsx", "Excel (*.xlsx)")
        if path:
            try:
                self.ctx.reserve.export_report(self._result, path)
            except Exception as exc:  # noqa: BLE001
                error = self.ctx.exceptions.report(exc)
                self._summary.setText(f"导出失败：{error.message}")
                self._summary.setProperty("role", "danger")
                return
            self._summary.setText(f"报告已导出：{path}")

    def _sensitivity(self) -> None:
        if self._result is None:
            self._summary.setText("请先计算储量")
            return
        try:
            metrics = self._metrics()
        except Exception as exc:  # noqa: BLE001
            self._fail_sensitivity(exc)
            return

        if self._runner is not None:
            self._sensitivity_btn.setEnabled(False)
            self._summary.setProperty("role", "hint")
            self._summary.setText("正在后台试算灵敏度…")
            self._runner.submit(
                lambda ctx: self.ctx.reserve.sensitivity(metrics, field="cutoff_grade"),
                name="reserve-sensitivity",
                on_finished=self._finish_sensitivity,
                on_failed=self._fail_sensitivity,
            )
            return
        try:
            rows = self.ctx.reserve.sensitivity(metrics, field="cutoff_grade")
        except Exception as exc:  # noqa: BLE001
            self._fail_sensitivity(exc)
            return
        self._finish_sensitivity(rows)

    def _finish_sensitivity(self, rows) -> None:
        from geomodel3d.app.events import RESERVE_SENSITIVITY

        self._sensitivity_btn.setEnabled(True)
        self.ctx.bus.publish(
            RESERVE_SENSITIVITY,
            {"rows": rows, "field": "cutoff_grade"},
            source="reserve",
        )
        self._summary.setProperty("role", "success")
        self._summary.setText("已生成灵敏度试算，见“储量图表”Tab")

    def _fail_sensitivity(self, exc: BaseException) -> None:
        self._sensitivity_btn.setEnabled(True)
        error = self.ctx.exceptions.report(exc)
        self._summary.setProperty("role", "danger")
        self._summary.setText(f"灵敏度试算失败：{error.message}")


def _spin(low: float, high: float, value: float, step: float) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(low, high)
    spin.setSingleStep(step)
    spin.setDecimals(2)
    spin.setValue(value)
    return spin


__all__ = ["ReserveDialog"]
