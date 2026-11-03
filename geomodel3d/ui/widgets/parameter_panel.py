"""上下文参数面板（7.4.4）。

分组折叠卡片：断层参数 / 插值算法 / 储量参数 / 显示设置。
数值控件采用滑块 + 数值输入联动；拖动实时预览，提交触发局部重建模。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from geomodel3d.config.schema import IMPLEMENTED_INTERP_METHODS, InterpConfig, ReserveConfig
from geomodel3d.domain.entities import Fault, FaultParams
from geomodel3d.ui.widgets.slider_spinbox import SliderSpinBox


class ParameterPanel(QScrollArea):
    """右侧上下文参数面板。"""

    faultApplied = pyqtSignal(str, object)
    faultPreview = pyqtSignal(str)
    faultCutRequested = pyqtSignal(str)
    faultReset = pyqtSignal(str)
    faultCreateRequested = pyqtSignal()
    interpChanged = pyqtSignal(object)
    reserveChanged = pyqtSignal(object)
    themeToggleRequested = pyqtSignal()
    resetViewRequested = pyqtSignal()
    clipChanged = pyqtSignal(str, float)
    exaggerationChanged = pyqtSignal(float)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("parameterPanel")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.viewport().setAutoFillBackground(False)
        self.setWidgetResizable(True)
        self._faults: List[Fault] = []
        self._guard = False

        container = QWidget()
        container.setObjectName("parameterContainer")
        container.setAutoFillBackground(False)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        self._fault_group, self._fault_controls = self._build_fault_group()
        layout.addWidget(self._fault_group)
        layout.addWidget(self._build_interp_group())
        layout.addWidget(self._build_reserve_group())
        layout.addWidget(self._build_display_group())
        layout.addStretch(1)
        self.setWidget(container)
        self.set_faults([])  # 初始无断层：禁用断层控件并显示提示

    # ------------------------------------------------------------------ #
    def _build_fault_group(self):
        group = QGroupBox("断层参数")
        form = QFormLayout(group)
        form.setLabelAlignment(form.labelAlignment())

        self._fault_hint = QLabel("未导入断层。可点击“新建断层”，或从 数据→导入断层。")
        self._fault_hint.setProperty("role", "hint")
        self._fault_hint.setWordWrap(True)
        form.addRow(self._fault_hint)

        create_row = QHBoxLayout()
        self._create_btn = QPushButton("新建断层")
        self._create_btn.clicked.connect(self.faultCreateRequested)
        create_row.addWidget(self._create_btn)
        create_row.addStretch(1)
        form.addRow(create_row)

        self._fault_selector = QComboBox()
        self._fault_selector.currentIndexChanged.connect(self._on_fault_selected)
        form.addRow("断层", self._fault_selector)

        self._fault_widgets = [self._fault_selector]
        controls: Dict[str, SliderSpinBox] = {}
        specs = [
            ("length", "延伸长度", 10.0, 10000.0, 1.0, "m"),
            ("depth_of_influence", "影响深度", 10.0, 5000.0, 1.0, "m"),
            ("dip", "倾角", 0.0, 90.0, 0.5, "°"),
            ("strike", "走向", 0.0, 359.0, 1.0, "°"),
            ("throw_v", "垂直落差", 0.0, 500.0, 0.5, "m"),
            ("throw_h", "水平落差", 0.0, 500.0, 0.5, "m"),
            ("buffer", "横向缓冲", 0.0, 500.0, 1.0, "m"),
        ]
        for key, label, low, high, step, unit in specs:
            widget = SliderSpinBox(label, minimum=low, maximum=high, step=step, unit=unit)
            controls[key] = widget
            self._fault_widgets.append(widget)
            form.addRow(widget)

        buttons = QHBoxLayout()
        self._preview_btn = QPushButton("仅预览影响域")
        self._preview_btn.clicked.connect(self._emit_preview)
        self._apply_btn = QPushButton("应用并重建模")
        self._apply_btn.setProperty("accent", True)
        self._apply_btn.clicked.connect(self._emit_apply)
        self._cut_btn = QPushButton("几何切割预览")
        self._cut_btn.clicked.connect(self._emit_cut)
        self._reset_btn = QPushButton("重置默认")
        self._reset_btn.clicked.connect(self._emit_reset)
        buttons.addWidget(self._preview_btn)
        buttons.addWidget(self._apply_btn)
        buttons.addWidget(self._cut_btn)
        buttons.addWidget(self._reset_btn)
        form.addRow(buttons)
        self._fault_widgets.extend([
            self._preview_btn, self._apply_btn, self._cut_btn, self._reset_btn
        ])
        return group, controls

    def _build_interp_group(self) -> QGroupBox:
        group = QGroupBox("插值算法")
        form = QFormLayout(group)
        self._method = QComboBox()
        self._method.addItems(list(IMPLEMENTED_INTERP_METHODS))
        self._method.setToolTip(
            "当前开源栈支持 idw / kriging / cokriging / dsi（开源近似）；GemPy 插值需扩展模块。"
        )
        self._method.currentTextChanged.connect(lambda _: self._emit_interp())
        form.addRow("方法", self._method)
        self._trend = SliderSpinBox("趋势强度", minimum=0.0, maximum=1.0, value=0.6, step=0.05)
        self._trend.valueChanged.connect(lambda _: self._emit_interp())
        form.addRow(self._trend)
        self._neighbor = SliderSpinBox("邻域点数", minimum=1, maximum=64, value=24, step=1, decimals=0)
        self._neighbor.valueChanged.connect(lambda _: self._emit_interp())
        form.addRow(self._neighbor)
        return group

    def _build_reserve_group(self) -> QGroupBox:
        group = QGroupBox("储量参数")
        form = QFormLayout(group)
        self._cutoff = SliderSpinBox("边界品位", minimum=0.0, maximum=10.0, value=0.8, step=0.1)
        self._cutoff.valueChanged.connect(lambda _: self._emit_reserve())
        form.addRow(self._cutoff)
        self._min_thickness = SliderSpinBox("最小可采厚度", minimum=0.0, maximum=10.0, value=0.8, step=0.1, unit="m")
        self._min_thickness.valueChanged.connect(lambda _: self._emit_reserve())
        form.addRow(self._min_thickness)
        self._density = SliderSpinBox("矿石体重", minimum=1.0, maximum=5.0, value=2.6, step=0.05, unit="t/m³")
        self._density.valueChanged.connect(lambda _: self._emit_reserve())
        form.addRow(self._density)
        return group

    def _build_display_group(self) -> QGroupBox:
        group = QGroupBox("显示设置")
        layout = QVBoxLayout(group)
        theme_btn = QPushButton("切换浅色 / 深色主题")
        theme_btn.clicked.connect(self.themeToggleRequested)
        reset_btn = QPushButton("复位视图 (R)")
        reset_btn.clicked.connect(self.resetViewRequested)
        layout.addWidget(theme_btn)
        layout.addWidget(reset_btn)

        clip_row = QHBoxLayout()
        clip_row.addWidget(QLabel("剖切轴"))
        self._clip_axis = QComboBox()
        self._clip_axis.addItems(["X", "Y", "Z"])
        self._clip_axis.currentTextChanged.connect(lambda _: self._emit_clip())
        clip_row.addWidget(self._clip_axis)
        layout.addLayout(clip_row)
        self._clip = SliderSpinBox("剖切位置", minimum=0.0, maximum=1.0, value=0.0, step=0.05)
        self._clip.valueChanged.connect(lambda _: self._emit_clip())
        layout.addWidget(self._clip)

        self._exaggeration = SliderSpinBox(
            "纵向放大 (Z)", minimum=0.0, maximum=20.0, value=0.0, step=0.5
        )
        self._exaggeration.setToolTip("0 = 自动（按水平/垂向跨度）；用于避免大面积薄层模型显示过扁。")
        self._exaggeration.valueChanged.connect(lambda _: self._emit_exaggeration())
        layout.addWidget(self._exaggeration)
        return group

    def _emit_exaggeration(self) -> None:
        if self._guard:
            return
        self.exaggerationChanged.emit(self._exaggeration.value())

    def _emit_clip(self) -> None:
        if self._guard:
            return
        self.clipChanged.emit(self._clip_axis.currentText().lower(), self._clip.value())

    # ------------------------------------------------------------------ #
    def set_faults(self, faults: List[Fault]) -> None:
        self._guard = True
        self._faults = list(faults)
        self._fault_selector.clear()
        for fault in self._faults:
            self._fault_selector.addItem(f"{fault.fault_id} {fault.name}".strip(), fault.fault_id)
        self._guard = False
        self._fault_group.setEnabled(True)
        if self._faults:
            self._fault_hint.setVisible(False)
            for widget in self._fault_widgets:
                widget.setEnabled(True)
            self._on_fault_selected(0)
        else:
            self._fault_hint.setVisible(True)
            for widget in self._fault_widgets:
                widget.setEnabled(False)

    def set_interp(self, config: InterpConfig) -> None:
        self._guard = True
        if self._method.findText(config.method) >= 0:
            self._method.setCurrentText(config.method)
        self._trend.set_value(config.trend_weight)
        self._neighbor.set_value(config.neighbor)
        self._guard = False

    def set_reserve(self, config: ReserveConfig) -> None:
        self._guard = True
        self._cutoff.set_value(config.cutoff_grade)
        self._min_thickness.set_value(config.min_thickness)
        self._density.set_value(config.density)
        self._guard = False

    def current_fault(self) -> Optional[Fault]:
        index = self._fault_selector.currentIndex()
        if 0 <= index < len(self._faults):
            return self._faults[index]
        return None

    def _on_fault_selected(self, index: int) -> None:
        if self._guard or not (0 <= index < len(self._faults)):
            return
        fault = self._faults[index]
        self._fault_group.setEnabled(True)
        for key, widget in self._fault_controls.items():
            widget.set_value(getattr(fault.params, key))

    def _collect_params(self) -> Optional[FaultParams]:
        fault = self.current_fault()
        if fault is None:
            return None
        return FaultParams(
            length=self._fault_controls["length"].value(),
            depth_of_influence=self._fault_controls["depth_of_influence"].value(),
            dip=self._fault_controls["dip"].value(),
            strike=self._fault_controls["strike"].value(),
            throw_v=self._fault_controls["throw_v"].value(),
            throw_h=self._fault_controls["throw_h"].value(),
            buffer=self._fault_controls["buffer"].value(),
        )

    def _emit_preview(self) -> None:
        fault = self.current_fault()
        if fault is not None:
            self.faultPreview.emit(fault.fault_id)

    def _emit_apply(self) -> None:
        fault = self.current_fault()
        params = self._collect_params()
        if fault is not None and params is not None:
            self.faultApplied.emit(fault.fault_id, params)

    def _emit_cut(self) -> None:
        fault = self.current_fault()
        if fault is not None:
            self.faultCutRequested.emit(fault.fault_id)

    def _emit_reset(self) -> None:
        fault = self.current_fault()
        if fault is not None:
            self._on_fault_selected(self._fault_selector.currentIndex())
            self.faultReset.emit(fault.fault_id)

    def _emit_interp(self) -> None:
        if self._guard:
            return
        self.interpChanged.emit(
            InterpConfig(
                method=self._method.currentText(),
                trend_weight=self._trend.value(),
                neighbor=int(self._neighbor.value()),
            )
        )

    def _emit_reserve(self) -> None:
        if self._guard:
            return
        self.reserveChanged.emit(
            ReserveConfig(
                cutoff_grade=self._cutoff.value(),
                min_thickness=self._min_thickness.value(),
                density=self._density.value(),
            )
        )


__all__ = ["ParameterPanel"]
