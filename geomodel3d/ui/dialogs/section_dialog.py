"""剖面向导对话框（M5 / 5.5）。"""
from __future__ import annotations

from typing import List, Optional

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from geomodel3d.domain.section import SectionResult
from geomodel3d.ui.async_runner import AsyncRunner


class SectionDialog(QDialog):
    """剖面类型选择、参数配置与成果导出。"""

    def __init__(self, context, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = context
        self.results: List[SectionResult] = []
        self.setWindowTitle("剖面向导")
        self.resize(560, 460)
        self._runner = AsyncRunner(context.tasks, self) if context.tasks is not None else None
        self._build_ui()

    def _build_ui(self) -> None:
        project = self.ctx.projects.project
        (xmin, ymin, zmin), (xmax, ymax, zmax) = project.bounds_list()
        layout = QVBoxLayout(self)

        self._kind = QComboBox()
        self._kind.addItems(["水平切面", "任意剖面线", "平行勘探线"])
        self._kind.currentIndexChanged.connect(self._on_kind)
        layout.addWidget(self._kind)

        form = QFormLayout()
        self._elevation = _spin(zmin, zmax, (zmin + zmax) / 2.0, 5.0)
        form.addRow("切面高程 (m)", self._elevation)

        self._x1 = _spin(xmin, xmax, xmin, 10.0)
        self._y1 = _spin(ymin, ymax, (ymin + ymax) / 2.0, 10.0)
        self._x2 = _spin(xmin, xmax, xmax, 10.0)
        self._y2 = _spin(ymin, ymax, (ymin + ymax) / 2.0, 10.0)
        form.addRow("起点 X", self._x1)
        form.addRow("起点 Y", self._y1)
        form.addRow("终点 X", self._x2)
        form.addRow("终点 Y", self._y2)

        self._spacing = _spin(10.0, 2000.0, 200.0, 10.0)
        form.addRow("勘探线间距 (m)", self._spacing)
        self._count = QSpinBox()
        self._count.setRange(1, 100)
        self._count.setValue(5)
        form.addRow("勘探线条数", self._count)
        self._offset = _spin(0.0, 500.0, 50.0, 5.0)
        form.addRow("钻孔投影窗口 (m)", self._offset)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self._run_btn = QPushButton("生成剖面")
        self._run_btn.setProperty("accent", True)
        self._run_btn.clicked.connect(self._run)
        self._export_btn = QPushButton("导出…")
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export)
        buttons.addWidget(self._run_btn)
        buttons.addWidget(self._export_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._status = QLabel("选择类型并配置参数后生成剖面")
        self._status.setProperty("role", "hint")
        layout.addWidget(self._status)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self._on_kind(0)

    def _on_kind(self, index: int) -> None:
        horizontal = index == 0
        line = index == 1
        batch = index == 2
        self._elevation.setEnabled(horizontal)
        for widget in (self._x1, self._y1, self._x2, self._y2):
            widget.setEnabled(line or batch)
        self._spacing.setEnabled(batch)
        self._count.setEnabled(batch)

    def _collect_request(self) -> dict:
        """在主线程读取控件值，避免后台线程直接访问 Qt 控件。"""
        index = self._kind.currentIndex()
        if index == 0:
            return {"kind": "horizontal", "elevation": self._elevation.value()}
        if index == 1:
            return {
                "kind": "arbitrary",
                "p1": [self._x1.value(), self._y1.value()],
                "p2": [self._x2.value(), self._y2.value()],
                "offset": self._offset.value(),
            }
        return {
            "kind": "batch",
            "p1": [self._x1.value(), self._y1.value()],
            "p2": [self._x2.value(), self._y2.value()],
            "offset": self._offset.value(),
            "spacing": self._spacing.value(),
            "count": self._count.value(),
        }

    def _compute_results(self, request: dict) -> List[SectionResult]:
        if request["kind"] == "horizontal":
            return [self.ctx.section.create_horizontal(float(request["elevation"]))]
        if request["kind"] == "arbitrary":
            return [
                self.ctx.section.create_arbitrary(
                    request["p1"],
                    request["p2"],
                    offset_window=float(request["offset"]),
                )
            ]
        base = self.ctx.section.create_arbitrary(
            request["p1"],
            request["p2"],
            offset_window=float(request["offset"]),
        )
        return self.ctx.section.batch(
            base.plan,
            spacing=float(request["spacing"]),
            count=int(request["count"]),
        )

    def _run(self) -> None:
        request = self._collect_request()
        if self._runner is not None:
            self._run_btn.setEnabled(False)
            self._status.setProperty("role", "hint")
            self._status.setText("正在后台生成剖面…")
            self._runner.submit(
                lambda ctx: self._compute_results(request),
                name="section",
                on_finished=self._finish_run,
                on_failed=self._fail_run,
            )
            return
        try:
            results = self._compute_results(request)
        except Exception as exc:  # noqa: BLE001
            self._fail_run(exc)
            return
        self._finish_run(results)

    def _finish_run(self, results: List[SectionResult]) -> None:
        self.results = list(results or [])
        self._run_btn.setEnabled(True)
        self._status.setProperty("role", "success")
        self._status.setText(f"已生成 {len(self.results)} 个剖面")
        self._export_btn.setEnabled(True)

    def _fail_run(self, exc: BaseException) -> None:
        self.results = []
        self._run_btn.setEnabled(True)
        self._export_btn.setEnabled(False)
        error = self.ctx.exceptions.report(exc)
        self._status.setText(f"生成失败：{error.message}")
        self._status.setProperty("role", "danger")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 协议
        if self._runner is not None:
            self._runner.detach()
        super().closeEvent(event)

    def _export(self) -> None:
        if not self.results:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出剖面", "section.geojson", "GeoJSON (*.geojson);;SHP (*.shp);;DXF (*.dxf);;SVG (*.svg);;PNG (*.png)"
        )
        if not path:
            return
        fmt = path.rsplit(".", 1)[-1].lower()
        jobs = [
            (
                result,
                path if len(self.results) == 1 else _suffix(path, index),
                fmt,
            )
            for index, result in enumerate(self.results)
        ]
        if self._runner is not None:
            self._export_btn.setEnabled(False)
            self._status.setProperty("role", "hint")
            self._status.setText("正在后台导出剖面…")
            self._runner.submit(
                lambda ctx: self._export_all(jobs),
                name="section-export",
                on_finished=self._finish_export,
                on_failed=self._fail_export,
            )
            return
        try:
            count = self._export_all(jobs)
        except Exception as exc:  # noqa: BLE001
            self._fail_export(exc)
            return
        self._finish_export(count)

    def _export_all(self, jobs) -> int:
        for result, target, fmt in jobs:
            self.ctx.section.export(result, target, fmt)
        return len(jobs)

    def _finish_export(self, count: int) -> None:
        self._export_btn.setEnabled(True)
        self._status.setProperty("role", "success")
        self._status.setText(f"已导出 {int(count)} 个剖面")

    def _fail_export(self, exc: BaseException) -> None:
        self._export_btn.setEnabled(True)
        error = self.ctx.exceptions.report(exc)
        self._status.setProperty("role", "danger")
        self._status.setText(f"导出失败：{error.message}")


def _spin(low: float, high: float, value: float, step: float) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(low, high)
    spin.setSingleStep(step)
    spin.setDecimals(1)
    spin.setValue(value)
    return spin


def _suffix(path: str, index: int) -> str:
    base, _, ext = path.rpartition(".")
    return f"{base}_{index + 1:02d}.{ext}"


__all__ = ["SectionDialog"]
