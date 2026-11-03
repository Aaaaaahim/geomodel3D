"""储量图表视图（M4 / 7.3 中央区）：分级汇总与指标灵敏度。"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from PyQt6.QtWidgets import QLabel, QStackedLayout, QWidget

from geomodel3d.domain.reserve import ReserveResult

logger = logging.getLogger(__name__)


class ReserveChartView(QWidget):
    """储量结果图表（matplotlib 双图）。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._stack = QStackedLayout(self)
        self._empty = QLabel("完成储量计算后将在此显示")
        self._empty.setProperty("role", "hint")
        self._empty.setAlignment(self._empty.alignment())
        self._stack.addWidget(self._empty)

        self._figure = None
        self._canvas = None
        self._init_canvas()
        if self._canvas is None:
            self._stack.setCurrentWidget(self._empty)
        else:
            self._stack.addWidget(self._canvas)
            self._stack.setCurrentWidget(self._canvas)

    def _init_canvas(self) -> None:
        try:
            from geomodel3d.dataio.charts import configure_matplotlib
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
        except Exception:  # noqa: BLE001 - 缺少 matplotlib Qt 后端，图表不可用
            logger.warning("储量图表后端不可用，回退占位显示", exc_info=True)
            return
        try:
            configure_matplotlib()
            self._figure = Figure(figsize=(8, 5), tight_layout=True)
            self._canvas = FigureCanvasQTAgg(self._figure)
        except Exception:  # noqa: BLE001
            self._figure = None
            self._canvas = None
            logger.warning("储量图表初始化失败", exc_info=True)

    def display(self, result: ReserveResult) -> None:
        if self._canvas is None or self._figure is None:
            self._empty.setText(result.summary_text())
            return
        self._figure.clear()
        axis = self._figure.add_subplot(111)
        classes = sorted(result.by_class)
        tonnage = [result.by_class[key].tonnage for key in classes]
        metal = [result.by_class[key].metal for key in classes]
        positions = range(len(classes))
        axis.bar([p - 0.2 for p in positions], tonnage, width=0.4, label="矿石量(t)", color="#2F6FB5")
        axis.bar([p + 0.2 for p in positions], metal, width=0.4, label="金属量", color="#E8913A")
        axis.set_xticks(list(positions))
        axis.set_xticklabels(classes)
        axis.set_title(f"{result.body_id} · {result.method} · {result.summary_text()}")
        axis.legend()
        self._canvas.draw_idle()
        self._stack.setCurrentWidget(self._canvas)

    def display_sensitivity(self, rows: List[Dict[str, float]], *, field: str = "cutoff_grade") -> None:
        if self._canvas is None or self._figure is None or not rows:
            return
        self._figure.clear()
        axis = self._figure.add_subplot(111)
        values = [row["value"] for row in rows]
        tonnage = [row["tonnage"] for row in rows]
        metal = [row["metal"] for row in rows]
        axis.plot(values, tonnage, "o-", color="#2F6FB5", label="矿石量(t)")
        axis2 = axis.twinx()
        axis2.plot(values, metal, "s--", color="#E8913A", label="金属量")
        axis.set_xlabel(field)
        axis.set_ylabel("矿石量(t)", color="#2F6FB5")
        axis2.set_ylabel("金属量", color="#E8913A")
        axis.set_title(f"指标灵敏度：{field}")
        self._canvas.draw_idle()
        self._stack.setCurrentWidget(self._canvas)

    def clear(self) -> None:
        if self._figure is not None:
            self._figure.clear()
            self._canvas.draw_idle()
        self._stack.setCurrentWidget(self._empty)


__all__ = ["ReserveChartView"]
