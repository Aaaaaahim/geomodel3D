"""剖面视图（7.3 中央区）：matplotlib 渲染剖面迹线与钻孔。"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from geomodel3d.domain.section import SectionResult


class SectionView(QWidget):
    """二维剖面显示。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._canvas = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._placeholder = QLabel("生成剖面后将在此显示")
        self._placeholder.setProperty("role", "hint")
        self._layout.addWidget(self._placeholder)
        self._init_canvas()

    def _init_canvas(self) -> None:
        try:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
        except Exception:
            return
        figure = Figure(figsize=(8, 5), tight_layout=True)
        canvas = FigureCanvasQTAgg(figure)
        self._figure = figure
        self._canvas = canvas
        self._layout.addWidget(canvas)

    def display(self, result: SectionResult) -> None:
        if self._canvas is None:
            self._placeholder.setText(
                f"{result.plan.name}：{sum(len(v) for v in result.polylines.values())} 条迹线"
            )
            return
        self._placeholder.hide()
        figure = self._figure
        figure.clear()
        axis = figure.add_subplot(111)
        palette = ["#7FA8C9", "#D9CBB8", "#6B7A8D", "#E8913A", "#2F9E63", "#9B7FB0"]
        for index, (code, polylines) in enumerate(sorted(result.polylines.items())):
            color = palette[index % len(palette)]
            for line in polylines:
                xs = [point[0] for point in line]
                ys = [point[1] for point in line]
                axis.plot(xs, ys, color=color, lw=1.2, label=code if line is polylines[0] else None)
        for hole in result.boreholes:
            axis.axvline(hole.u, color="#9AA7B4", lw=0.6, ls="--")
            axis.text(hole.u, hole.collar_v, hole.hole_id, fontsize=7, rotation=90, va="bottom")
        for fault_id, trace in result.fault_traces.items():
            xs = [point[0] for point in trace]
            ys = [point[1] for point in trace]
            axis.plot(xs, ys, color="#E8913A", lw=1.6, ls="-.")
            axis.text(xs[0], ys[0], fault_id, color="#E8913A", fontsize=8)
        for annotation in result.annotations():
            if annotation["kind"] == "fault":
                continue
            color = "#2B3440" if annotation["kind"] == "layer" else "#5A6B7C"
            size = 8 if annotation["kind"] == "layer" else 6.5
            axis.text(annotation["u"], annotation["v"], annotation["text"], fontsize=size, color=color)
        axis.set_aspect("equal", adjustable="box")
        axis.set_xlabel("距离 u (m)")
        axis.set_ylabel("高程 v (m)")
        axis.set_title(result.plan.name)
        axis.grid(True, ls=":", alpha=0.4)
        if result.polylines:
            axis.legend(loc="best", fontsize=8)
        self._canvas.draw_idle()

    def clear(self) -> None:
        if self._canvas is not None:
            self._figure.clear()
            self._canvas.draw_idle()
        self._placeholder.show()


__all__ = ["SectionView"]
