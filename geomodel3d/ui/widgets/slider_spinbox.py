"""滑块 + 数值输入联动控件（7.4.4）。"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QWidget,
)

_SCALE = 100


class SliderSpinBox(QWidget):
    """滑块与数值框双向联动，带单位与范围校验。"""

    valueChanged = pyqtSignal(float)

    def __init__(
        self,
        label: str,
        *,
        minimum: float = 0.0,
        maximum: float = 100.0,
        value: float = 0.0,
        decimals: int = 2,
        step: float = 1.0,
        unit: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._decimals = decimals
        self._unit = unit
        self._guard = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._label = QLabel(label)
        self._label.setMinimumWidth(72)
        layout.addWidget(self._label)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(int(round(minimum * _SCALE)))
        self._slider.setMaximum(int(round(maximum * _SCALE)))
        self._slider.setSingleStep(int(round(step * _SCALE)) or 1)
        layout.addWidget(self._slider, 1)

        self._spin = QDoubleSpinBox()
        self._spin.setDecimals(decimals)
        self._spin.setRange(minimum, maximum)
        self._spin.setSingleStep(step)
        self._spin.setSuffix(f" {unit}" if unit else "")
        self._spin.setMinimumWidth(96)
        layout.addWidget(self._spin)

        self._slider.valueChanged.connect(self._on_slider)
        self._spin.valueChanged.connect(self._on_spin)
        self.set_value(value)

    # ------------------------------------------------------------------ #
    def value(self) -> float:
        return float(self._spin.value())

    def set_value(self, value: float) -> None:
        self._guard = True
        self._spin.setValue(float(value))
        self._slider.setValue(int(round(float(value) * _SCALE)))
        self._guard = False

    def set_range(self, minimum: float, maximum: float) -> None:
        self._slider.setMinimum(int(round(minimum * _SCALE)))
        self._slider.setMaximum(int(round(maximum * _SCALE)))
        self._spin.setRange(minimum, maximum)

    def _on_slider(self, raw: int) -> None:
        if self._guard:
            return
        self._guard = True
        self._spin.setValue(raw / _SCALE)
        self._guard = False
        self.valueChanged.emit(self.value())

    def _on_spin(self, value: float) -> None:
        if self._guard:
            return
        self._guard = True
        self._slider.setValue(int(round(value * _SCALE)))
        self._guard = False
        self.valueChanged.emit(self.value())


__all__ = ["SliderSpinBox"]
