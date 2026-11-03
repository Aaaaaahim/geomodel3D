"""配置层：schema、默认值、错误码、用户设置存储。"""
from geomodel3d.config.defaults import (
    APP_DISPLAY_NAME,
    APP_NAME,
    PROJECT_SUFFIX,
    user_config_dir,
    user_settings_path,
)
from geomodel3d.config.errors import (
    ConfigurationError,
    EngineUnavailableError,
    ErrorCode,
    GeoModelError,
    ProjectFormatError,
)
from geomodel3d.config.schema import (
    AppSettings,
    Bounds,
    CRSConfig,
    InterpConfig,
    ProjectConfig,
    ProjectMeta,
    ReserveConfig,
    StratigraphyEntry,
)
from geomodel3d.config.settings_store import load_settings, save_settings

__all__ = [
    "APP_NAME",
    "APP_DISPLAY_NAME",
    "PROJECT_SUFFIX",
    "user_config_dir",
    "user_settings_path",
    "AppSettings",
    "Bounds",
    "CRSConfig",
    "InterpConfig",
    "ProjectConfig",
    "ProjectMeta",
    "ReserveConfig",
    "StratigraphyEntry",
    "ErrorCode",
    "GeoModelError",
    "ConfigurationError",
    "ProjectFormatError",
    "EngineUnavailableError",
    "load_settings",
    "save_settings",
]
