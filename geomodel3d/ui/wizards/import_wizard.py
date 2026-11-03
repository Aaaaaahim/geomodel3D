"""钻孔导入向导（M1-F1 / 7.6 QWizard）。

多页：文件 -> 列映射 -> 预览 -> 坐标系 -> 校验结果。
向导只负责收集配置，实际导入由主窗口调用 DataService 完成。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from geomodel3d.dataio.borehole_io import REQUIRED, detect_mapping, read_table
from geomodel3d.dataio.crs_detect import detect_crs

_LABELS = {
    "hole_id": "孔号 *",
    "x": "X 坐标 *",
    "y": "Y 坐标 *",
    "collar_elev": "孔口高程 *",
    "layer_top": "层顶深",
    "layer_base": "层底深",
    "layer_top_elev": "层顶标高",
    "layer_base_elev": "层底标高",
    "layer_code": "地层代码 *",
    "thickness": "厚度",
    "grade": "品位",
    "grade_unit": "品位单位",
}


class ImportBoreholeWizard(QWizard):
    """钻孔导入向导。"""

    def __init__(self, parent=None, *, default_crs: Optional[int] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("导入钻孔数据")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.resize(760, 560)
        self._path: Optional[Path] = None
        self._columns: List[str] = []
        self._load_error = ""
        self._combos: Dict[str, QComboBox] = {}
        self._frame = None

        self._file_page = _FilePage(self)
        self._mapping_page = _MappingPage(self)
        self._preview_page = _PreviewPage(self)
        self._crs_page = _CRSPage(self, default_crs)
        self.addPage(self._file_page)
        self.addPage(self._mapping_page)
        self.addPage(self._preview_page)
        self.addPage(self._crs_page)

    # ------------------------------------------------------------------ #
    def load_path(self, path: Path) -> bool:
        path = Path(path)
        self._path = None
        self._columns = []
        self._load_error = ""
        try:
            frame = read_table(path)
        except Exception as exc:  # noqa: BLE001
            self._load_error = str(exc)
            return False
        self._path = path
        self._columns = [str(c) for c in frame.columns]
        self._frame = frame
        detected = detect_mapping(self._columns)
        self._mapping_page.populate(self._columns, detected)
        self._preview_page.populate(frame.head(200))
        self.update_crs_suggestion()
        return True

    def update_crs_suggestion(self) -> None:
        """根据当前列映射对坐标做 EPSG 自动识别，并在 CRS 页给出建议。"""
        if self._frame is None:
            return
        x_col = self._combo_data("x")
        y_col = self._combo_data("y")
        if not x_col or not y_col:
            self._crs_page.set_suggestion(None)
            return
        try:
            xs = pd.to_numeric(self._frame[x_col], errors="coerce")
            ys = pd.to_numeric(self._frame[y_col], errors="coerce")
        except Exception:  # noqa: BLE001
            self._crs_page.set_suggestion(None)
            return
        import numpy as np

        points = np.column_stack([xs.to_numpy(), ys.to_numpy()])
        points = points[np.isfinite(points).all(axis=1)]
        self._crs_page.set_suggestion(points if len(points) else None)

    def _combo_data(self, key: str) -> Optional[str]:
        combo = self._combos.get(key)
        return combo.currentData() if combo is not None else None

    def result_config(self) -> Tuple[Path, Dict[str, Optional[str]], Optional[int]]:
        mapping = {
            key: (combo.currentData() or None)
            for key, combo in self._combos.items()
        }
        return self._path, mapping, self._crs_page.epsg()

    def _register_combo(self, key: str, combo: QComboBox) -> None:
        self._combos[key] = combo

    def _crs_page_widget(self) -> "_CRSPage":
        return self._crs_page


class _FilePage(QWizardPage):
    def __init__(self, wizard: ImportBoreholeWizard) -> None:
        super().__init__()
        self._wizard = wizard
        self.setTitle("选择数据文件")
        self.setSubTitle("支持 CSV / Excel，系统将自动识别列名。")
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self._edit = QLineEdit()
        self._edit.setReadOnly(True)
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse)
        row.addWidget(self._edit)
        row.addWidget(browse)
        layout.addLayout(row)
        layout.addWidget(QLabel("必填列：孔号、X、Y、孔口高程、地层代码，以及（层顶深+层底深）或（层顶标高+层底标高）"))
        layout.addStretch(1)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择钻孔文件", "", "数据文件 (*.csv *.xlsx *.xls);;所有文件 (*.*)"
        )
        if path:
            if self._wizard.load_path(Path(path)):
                self._edit.setText(path)
                self.completeChanged.emit()
            else:
                reason = self._wizard._load_error or "未知原因"
                self._edit.setText(f"无法读取: {path}（{reason}）")
                self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self._wizard._path is not None

    def nextId(self) -> int:
        return super().nextId()


class _MappingPage(QWizardPage):
    def __init__(self, wizard: ImportBoreholeWizard) -> None:
        super().__init__()
        self._wizard = wizard
        self.setTitle("列映射")
        self.setSubTitle("把文件列对应到标准字段，带 * 为必填。")
        self._form = QFormLayout(self)

    def populate(self, columns: List[str], detected: Dict[str, Optional[str]]) -> None:
        while self._form.rowCount():
            self._form.removeRow(0)
        self._wizard._combos.clear()
        for key, label in _LABELS.items():
            combo = QComboBox()
            combo.addItem("(无)", None)
            for column in columns:
                combo.addItem(column, column)
            target = detected.get(key)
            if target:
                index = combo.findData(target)
                if index >= 0:
                    combo.setCurrentIndex(index)
            combo.currentIndexChanged.connect(self.completeChanged)
            if key in ("x", "y"):
                combo.currentIndexChanged.connect(lambda _=0: self._wizard.update_crs_suggestion())
            self._form.addRow(label, combo)
            self._wizard._register_combo(key, combo)

    def isComplete(self) -> bool:
        for key in REQUIRED:
            combo = self._wizard._combos.get(key)
            if combo is None or not combo.currentData():
                return False
        has_depth = all(
            self._wizard._combos.get(k) and self._wizard._combos[k].currentData()
            for k in ("layer_top", "layer_base")
        )
        has_elev = all(
            self._wizard._combos.get(k) and self._wizard._combos[k].currentData()
            for k in ("layer_top_elev", "layer_base_elev")
        )
        return bool(has_depth or has_elev)


class _PreviewPage(QWizardPage):
    def __init__(self, wizard: ImportBoreholeWizard) -> None:
        super().__init__()
        self.setTitle("数据预览")
        self.setSubTitle("仅显示前 200 行。")
        layout = QVBoxLayout(self)
        self._table = QTableWidget()
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table)

    def populate(self, frame) -> None:
        self._table.clear()
        self._table.setColumnCount(len(frame.columns))
        self._table.setRowCount(len(frame))
        self._table.setHorizontalHeaderLabels([str(c) for c in frame.columns])
        for row in range(len(frame)):
            for column in range(len(frame.columns)):
                value = frame.iat[row, column]
                self._table.setItem(row, column, QTableWidgetItem("" if value is None else str(value)))


class _CRSPage(QWizardPage):
    def __init__(self, wizard: ImportBoreholeWizard, default_crs: Optional[int]) -> None:
        super().__init__()
        self._wizard = wizard
        self.setTitle("坐标系")
        self.setSubTitle("指定源数据坐标系（EPSG），用于统一到工程 CRS。已按坐标自动识别。")
        layout = QFormLayout(self)
        self._epsg = QSpinBox()
        self._epsg.setRange(0, 999999)
        self._epsg.setSpecialValueText("未指定")
        self._epsg.setValue(default_crs or 0)
        layout.addRow("EPSG 编码", self._epsg)
        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._hint.setProperty("role", "hint")
        layout.addRow(self._hint)
        self._auto = True
        self._epsg.valueChanged.connect(self._on_manual_change)

    def set_suggestion(self, points) -> None:
        """根据坐标设置自动识别建议；用户已手动修改过则不再覆盖。"""
        if points is None:
            self._hint.setText("未能自动识别坐标系，请手动填写 EPSG。")
            return
        suggestion = detect_crs(points)[0]
        lines = [suggestion.name]
        if suggestion.epsg:
            lines.append(f"EPSG:{suggestion.epsg}")
        lines.append(f"置信度：{suggestion.confidence}（{suggestion.reason}）")
        self._hint.setText("自动识别：" + " · ".join(lines))
        if suggestion.usable and self._auto:
            self._epsg.setValue(int(suggestion.epsg))

    def _on_manual_change(self, value: int) -> None:
        # 用户手动改动后不再被自动结果覆盖
        self._auto = False

    def epsg(self) -> Optional[int]:
        value = self._epsg.value()
        return value or None


__all__ = ["ImportBoreholeWizard"]
