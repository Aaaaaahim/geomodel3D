"""默认值、路径与常量。"""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "GeoModel3D"
APP_ORG = "GeoModel3D"
APP_DISPLAY_NAME = "三维地质建模系统"
PROJECT_SUFFIX = ".gm3d"
SETTINGS_FILENAME = "settings.json"


def user_config_dir() -> Path:
    """返回用户级配置目录（跨平台）。

    Windows: %APPDATA%/GeoModel3D
    其它:    ~/.config/geomodel3d
    """
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME.lower()


def user_settings_path() -> Path:
    return user_config_dir() / SETTINGS_FILENAME


def user_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    return user_config_dir() / "data"


__all__ = [
    "APP_NAME",
    "APP_ORG",
    "APP_DISPLAY_NAME",
    "PROJECT_SUFFIX",
    "SETTINGS_FILENAME",
    "user_config_dir",
    "user_settings_path",
    "user_data_dir",
]
