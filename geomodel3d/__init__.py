"""geomodel3d — 基于 Python + GemPy + OpenGeode 的三维地质建模软件。"""

import os as _os
import sys as _sys

__version__ = "0.1.0"
__schema_version__ = "2.0"


def _preload_opengeode() -> None:
    """尽早加载 OpenGeode 原生库（仅源码运行时）。

    OpenGeode 的本地依赖若在 PyVista/VTK、GDAL、matplotlib 之后加载，
    在部分 Windows 环境下会出现 DLL 访问冲突（access violation）。
    因此这里在包导入时（早于可视化库）做一次尽力而为的预加载。

    打包（PyInstaller 冻结）后不预加载：OpenGeode 为可选引擎，其原生库
    在冻结环境下可能导致崩溃，冻结版默认不启用（引擎自动回退 Mock/OpenGeode）。
    可通过环境变量 ``GEOMODEL3D_SKIP_OG_PRELOAD`` 关闭。
    """
    if getattr(_sys, "frozen", False) or _os.environ.get("GEOMODEL3D_SKIP_OG_PRELOAD"):
        return
    try:
        import opengeode  # noqa: F401
    except Exception:  # noqa: BLE001 - 未安装或加载失败时忽略
        pass


_preload_opengeode()

__all__ = ["__version__", "__schema_version__"]
