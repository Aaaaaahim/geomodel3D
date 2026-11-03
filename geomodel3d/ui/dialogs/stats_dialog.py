"""统计分析对话框（M7 / 5.7）。"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from geomodel3d.dataio.charts import configure_matplotlib
from geomodel3d.ui.async_runner import AsyncRunner


class StatsDialog(QDialog):
    """厚度/品位/断层/质量/体积统计与图表导出。"""

    def __init__(self, context, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = context
        self._payload: Dict[str, object] = {}
        self.setWindowTitle("统计分析")
        self.resize(900, 620)
        self._runner = AsyncRunner(context.tasks, self) if context.tasks is not None else None
        self._build_ui()
        self._reload()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self._kind = QComboBox()
        self._kind.addItems(["厚度分布", "品位分布", "断层统计", "数据质量", "地层体积"])
        self._kind.currentIndexChanged.connect(lambda _: self._draw())
        header.addWidget(QLabel("图表"))
        header.addWidget(self._kind)
        header.addStretch(1)
        self._export_charts_btn = QPushButton("导出图表…")
        self._export_charts_btn.clicked.connect(self._export_charts)
        self._export_excel_btn = QPushButton("导出报表…")
        self._export_excel_btn.clicked.connect(self._export_excel)
        header.addWidget(self._export_charts_btn)
        header.addWidget(self._export_excel_btn)
        layout.addLayout(header)

        try:
            plt = configure_matplotlib()
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure

            self._figure = Figure(figsize=(8, 5), tight_layout=True)
            self._canvas = FigureCanvasQTAgg(self._figure)
            layout.addWidget(self._canvas)
            self._plt = plt
        except Exception:
            self._figure = None
            self._canvas = None
            self._plt = None
            layout.addWidget(QLabel("图表组件不可用（缺少 matplotlib）"))

        self._summary = QLabel("")
        self._summary.setProperty("role", "hint")
        layout.addWidget(self._summary)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    # ------------------------------------------------------------------ #
    def _reload(self) -> None:
        if self._runner is not None:
            self._summary.setProperty("role", "hint")
            self._summary.setText("正在后台统计…")
            self._runner.submit(
                lambda ctx: self.ctx.stats.collect(),
                name="stats",
                on_finished=self._finish_reload,
                on_failed=self._fail_reload,
            )
            return
        try:
            payload = self.ctx.stats.collect()
        except Exception as exc:  # noqa: BLE001
            self._fail_reload(exc)
            return
        self._finish_reload(payload)

    def _finish_reload(self, payload: Dict[str, object]) -> None:
        self._payload = payload or {}
        self._draw()

    def _fail_reload(self, exc: BaseException) -> None:
        error = self.ctx.exceptions.report(exc)
        self._summary.setProperty("role", "danger")
        self._summary.setText(f"统计失败：{error.message}")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 协议
        if self._runner is not None:
            self._runner.detach()
        super().closeEvent(event)

    def _draw(self) -> None:
        if self._canvas is None or self._figure is None or not self._payload:
            return
        self._figure.clear()
        axis = self._figure.add_subplot(111)
        index = self._kind.currentIndex()
        if index == 0:
            self._draw_thickness(axis)
        elif index == 1:
            self._draw_grade(axis)
        elif index == 2:
            self._draw_faults(axis)
        elif index == 3:
            self._draw_quality(axis)
        else:
            self._draw_volume(axis)
        self._canvas.draw_idle()

    def _draw_thickness(self, axis) -> None:
        stats = self._payload.get("thickness", {})
        for code, entry in stats.items():
            hist = entry["histogram"]
            axis.bar(hist["centers"], hist["counts"], alpha=0.6, label=code)
        axis.set_title("地层厚度分布")
        axis.set_xlabel("厚度 (m)")
        axis.set_ylabel("频数")
        if stats:
            axis.legend()
        self._summary.setText(f"共 {len(stats)} 个地层")

    def _draw_grade(self, axis) -> None:
        stats = self._payload.get("grade", {})
        hist = stats.get("histogram", {"centers": [], "counts": []})
        axis.bar(hist["centers"], hist["counts"], color="#2F9E63")
        axis.set_title(f"品位分布 ({stats.get('unit', '')})")
        axis.set_xlabel("品位")
        axis.set_ylabel("频数")
        summary = stats.get("summary", {})
        self._summary.setText(
            f"样本 {summary.get('count', 0)} · 均值 {summary.get('mean', 0):.3f} · "
            f"标准差 {summary.get('std', 0):.3f}"
        )

    def _draw_faults(self, axis) -> None:
        stats = self._payload.get("faults", {})
        rose = stats.get("rose", {"centers": [], "counts": []})
        axis.bar(rose["centers"], rose["counts"], width=8.0, color="#E8913A")
        axis.set_title("断层走向玫瑰图 (°)")
        axis.set_xlabel("走向")
        axis.set_ylabel("条数")
        self._summary.setText(f"断层 {stats.get('count', 0)} 条")

    def _draw_quality(self, axis) -> None:
        stats = self._payload.get("quality", {})
        by_code = stats.get("by_code", {})
        if by_code:
            axis.bar(list(by_code), list(by_code.values()), color="#D64550")
        axis.set_title("数据质量问题分布")
        axis.set_ylabel("数量")
        summary = stats.get("summary", {})
        self._summary.setText(
            f"错误 {summary.get('error', 0)} · 告警 {summary.get('warning', 0)}"
        )

    def _draw_volume(self, axis) -> None:
        volumes = self._payload.get("layer_volumes", {})
        if volumes:
            axis.bar(list(volumes), list(volumes.values()), color="#7FA8C9")
        axis.set_title("地层体积")
        axis.set_ylabel("体积 (m³)")
        self._summary.setText(f"共 {len(volumes)} 个地层")

    # ------------------------------------------------------------------ #
    def _export_charts(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择图表输出目录")
        if not directory:
            return
        if self._runner is not None:
            self._export_charts_btn.setEnabled(False)
            self._summary.setProperty("role", "hint")
            self._summary.setText("正在后台导出图表…")
            self._runner.submit(
                lambda ctx: self.ctx.stats.export_charts(Path(directory), fmt="png"),
                name="stats-charts",
                on_finished=lambda outputs: self._finish_charts(outputs, directory),
                on_failed=self._fail_export,
            )
            return
        try:
            outputs = self.ctx.stats.export_charts(Path(directory), fmt="png")
        except Exception as exc:  # noqa: BLE001
            self._fail_export(exc)
            return
        self._finish_charts(outputs, directory)

    def _finish_charts(self, outputs, directory: str) -> None:
        self._export_charts_btn.setEnabled(True)
        self._summary.setProperty("role", "success")
        self._summary.setText(f"已导出 {len(outputs)} 张图表到 {directory}")

    def _export_excel(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出统计报表", "statistics.xlsx", "Excel (*.xlsx)"
        )
        if not path:
            return
        if self._runner is not None:
            self._export_excel_btn.setEnabled(False)
            self._summary.setProperty("role", "hint")
            self._summary.setText("正在后台导出统计报表…")
            self._runner.submit(
                lambda ctx: self.ctx.stats.export_excel(Path(path)),
                name="stats-excel",
                on_finished=lambda output: self._finish_excel(output, path),
                on_failed=self._fail_export,
            )
            return
        try:
            output = self.ctx.stats.export_excel(Path(path))
        except Exception as exc:  # noqa: BLE001
            self._fail_export(exc)
            return
        self._finish_excel(output, path)

    def _finish_excel(self, output, path: str) -> None:
        self._export_excel_btn.setEnabled(True)
        self._summary.setProperty("role", "success")
        self._summary.setText(f"报表已导出：{output or path}")

    def _fail_export(self, exc: BaseException) -> None:
        self._export_charts_btn.setEnabled(True)
        self._export_excel_btn.setEnabled(True)
        error = self.ctx.exceptions.report(exc)
        self._summary.setProperty("role", "danger")
        self._summary.setText(f"导出失败：{error.message}")


__all__ = ["StatsDialog"]
