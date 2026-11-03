"""日志面板（7.3 底部）：分级着色 + 级别过滤。"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

LEVEL_COLORS = {
    "DEBUG": "#8A97A5",
    "INFO": "#5A6B7C",
    "WARNING": "#E8913A",
    "ERROR": "#D64550",
    "CRITICAL": "#D64550",
}
LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


class LogPanel(QWidget):
    """结构化日志显示。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._min_level = "INFO"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.addWidget(QLabel("日志级别"))
        self._level_box = QComboBox()
        self._level_box.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self._level_box.setCurrentText("INFO")
        self._level_box.currentTextChanged.connect(self._on_level_changed)
        header.addWidget(self._level_box)
        header.addStretch(1)
        layout.addLayout(header)

        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setMaximumBlockCount(5000)
        layout.addWidget(self._view)

    def append(self, level: str, message: str, timestamp: str = "") -> None:
        if LEVEL_ORDER.get(level, 0) < LEVEL_ORDER.get(self._min_level, 0):
            return
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(LEVEL_COLORS.get(level, "#5A6B7C")))
        cursor = self._view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        prefix = f"{timestamp} " if timestamp else ""
        cursor.insertText(f"{prefix}[{level}] {message}\n", fmt)
        self._view.setTextCursor(cursor)
        self._view.ensureCursorVisible()

    def clear(self) -> None:
        self._view.clear()

    def _on_level_changed(self, level: str) -> None:
        self._min_level = level


__all__ = ["LogPanel"]
