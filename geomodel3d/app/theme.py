"""主题加载与应用（7.2）。"""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

logger = logging.getLogger(__name__)

THEME_DIR = Path(__file__).resolve().parent.parent / "config" / "themes"

_BACKGROUNDS = {"light": "#F5F7FA", "dark": "#1E242C"}

_PALETTES = {
    "light": {
        "Window": "#F5F7FA",
        "WindowText": "#2B3440",
        "Base": "#FFFFFF",
        "AlternateBase": "#F7F9FC",
        "ToolTipBase": "#2B3440",
        "ToolTipText": "#FFFFFF",
        "Text": "#2B3440",
        "Button": "#FFFFFF",
        "ButtonText": "#2B3440",
        "BrightText": "#FFFFFF",
        "Highlight": "#D3E3F5",
        "HighlightedText": "#2B3440",
        "Link": "#2F6FB5",
        "LinkVisited": "#6B7A8D",
        "PlaceholderText": "#9AA7B4",
        "DisabledText": "#9AA7B4",
    },
    "dark": {
        "Window": "#1E242C",
        "WindowText": "#E3E9F0",
        "Base": "#232B34",
        "AlternateBase": "#28313B",
        "ToolTipBase": "#E3E9F0",
        "ToolTipText": "#1E242C",
        "Text": "#E3E9F0",
        "Button": "#2B343F",
        "ButtonText": "#E3E9F0",
        "BrightText": "#FFFFFF",
        "Highlight": "#3B5474",
        "HighlightedText": "#FFFFFF",
        "Link": "#6FA8E0",
        "LinkVisited": "#9AA7B4",
        "PlaceholderText": "#6B7787",
        "DisabledText": "#6B7787",
    },
}


def load_stylesheet(theme: str) -> str:
    """读取主题 QSS 文本。"""
    path = THEME_DIR / f"{theme}.qss"
    if not path.exists():
        logger.warning("主题文件不存在: %s，回退浅色", path)
        path = THEME_DIR / "light.qss"
    return path.read_text(encoding="utf-8")


def apply_theme(app: QApplication, theme: str) -> None:
    """应用主题 QSS 与完整调色板。

    仅切换 QSS 时，未被样式覆盖的 QScrollArea 视口、滑块、下拉列表等
    仍会沿用旧调色板；这里同时重建主要 Palette 角色，并用 Fusion 作为
    跨平台基线，避免深色主题下残留浅色控件。
    """
    theme = theme if theme in _PALETTES else "light"
    app.setProperty("theme", theme)
    app.setStyle("Fusion")

    colors = _PALETTES[theme]
    palette = QPalette()
    for name, color in colors.items():
        if name == "DisabledText":
            continue
        role = getattr(QPalette.ColorRole, name, None)
        if role is not None:
            palette.setColor(role, QColor(color))
    disabled = QColor(colors["DisabledText"])
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.PlaceholderText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    app.setPalette(palette)
    app.setStyleSheet(load_stylesheet(theme))


def viewport_background(theme: str) -> str:
    """三维视口背景色（与主题联动）。"""
    return "#FFFFFF" if theme == "light" else "#1E242C"


def accent_color(theme: str) -> str:
    """断层编辑/重点操作的强调色。"""
    return "#E8913A" if theme == "light" else "#F0A75A"


__all__ = ["apply_theme", "load_stylesheet", "viewport_background", "accent_color", "THEME_DIR"]
