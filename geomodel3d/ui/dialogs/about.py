"""关于对话框。"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget

from geomodel3d import __version__
from geomodel3d.config.defaults import APP_DISPLAY_NAME


class AboutDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("关于")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        title = QLabel(APP_DISPLAY_NAME)
        title.setProperty("role", "title")
        layout.addWidget(title)
        layout.addWidget(QLabel(f"版本 {__version__}"))
        layout.addWidget(
            QLabel("技术栈：Python + PyQt6 + PyVista + GemPy + OpenGeode\n"
                   "本软件为桌面端三维地质建模系统。")
        )
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


__all__ = ["AboutDialog"]
