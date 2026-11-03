"""错误对话框（7.5.2）：原因 + 建议 + 详情折叠。"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ErrorDialog(QDialog):
    """统一错误提示。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        suggestion: str = "",
        detail: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("操作失败")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)

        title = QLabel(message)
        title.setWordWrap(True)
        title.setProperty("role", "danger")
        layout.addWidget(title)

        if code:
            layout.addWidget(QLabel(f"错误码：{code}"))
        if suggestion:
            hint = QLabel(f"建议：{suggestion}")
            hint.setWordWrap(True)
            hint.setProperty("role", "hint")
            layout.addWidget(hint)

        self._detail = QPlainTextEdit(detail)
        self._detail.setReadOnly(True)
        self._detail.setVisible(False)
        layout.addWidget(self._detail)

        if detail:
            self._toggle = QPushButton("显示详情")
            self._toggle.setCheckable(True)
            self._toggle.toggled.connect(self._on_toggle_detail)
            layout.addWidget(self._toggle)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _on_toggle_detail(self, checked: bool) -> None:
        self._detail.setVisible(checked)
        self._toggle.setText("隐藏详情" if checked else "显示详情")


__all__ = ["ErrorDialog"]
