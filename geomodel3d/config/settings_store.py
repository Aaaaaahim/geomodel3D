"""用户设置的读写（原子写入）。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from geomodel3d.config.defaults import user_settings_path
from geomodel3d.config.errors import ConfigurationError
from geomodel3d.config.schema import AppSettings


def load_settings(path: Optional[Path] = None) -> AppSettings:
    """读取用户设置；文件不存在时返回默认值。"""
    target = Path(path) if path else user_settings_path()
    if not target.exists():
        return AppSettings()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"读取设置失败: {exc}", context={"path": str(target)}) from exc
    try:
        return AppSettings.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError
        raise ConfigurationError(
            f"设置内容非法: {exc}", context={"path": str(target)}
        ) from exc


def save_settings(settings: AppSettings, path: Optional[Path] = None) -> Path:
    """原子写入用户设置，返回实际路径。"""
    target = Path(path) if path else user_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = settings.model_dump(mode="json")
    _atomic_write_json(target, payload)
    return target


def _atomic_write_json(target: Path, payload: dict) -> None:
    """临时文件 + 替换，避免写坏配置（3.5 原子替换思想）。"""
    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


__all__ = ["load_settings", "save_settings"]
