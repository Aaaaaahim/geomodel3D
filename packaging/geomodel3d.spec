# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（M7 骨架）。

用法：
    pyinstaller packaging/geomodel3d.spec

默认单目录发布（依赖体积大，单文件启动慢）。如需单文件，
把 EXE(...) 的 `exclude_binaries` 与 COLLECT 去掉即可。
"""
from pathlib import Path

from PyInstaller.building.datastruct import TOC
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "geomodel3d" / "config" / "themes"), "geomodel3d/config/themes"),
    # 外部 GemPy 桥接脚本需以真实 .py 文件随包分发（由外部解释器执行）
    (str(ROOT / "geomodel3d" / "engine" / "_gempy_bridge.py"), "geomodel3d/engine"),
]
# 可选资源目录：仅在存在且非空时加入，避免空目录导致打包失败
_assets = ROOT / "geomodel3d" / "assets"
if _assets.exists() and any(_assets.iterdir()):
    datas.append((str(_assets), "geomodel3d/assets"))
datas += collect_data_files("pyvista")

hiddenimports = collect_submodules("pyvistaqt")
# pkg_resources 运行时钩子依赖 setuptools 内置的 jaraco（命名空间在 setuptools._vendor），
# 必须显式收集，否则冻结后启动报 "No module named 'jaraco'"。
hiddenimports += [
    "pkg_resources",
    "jaraco",
    "jaraco.text",
    "jaraco.context",
    "jaraco.functools",
]
hiddenimports += collect_submodules("setuptools._vendor.jaraco")

a = Analysis(
    [str(ROOT / "geomodel3d" / "app" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib.tests",
        # 仅使用 PyQt6，排除环境中的其它 Qt 绑定，避免 PyInstaller 冲突
        "PyQt5",
        "PyQt5.QtCore",
        "PyQt5.QtGui",
        "PyQt5.QtWidgets",
        "PySide2",
        "PySide6",
        "shiboken2",
        "shiboken6",
        # 精简体积：排除本软件不使用的重型/无关依赖
        # 注意：不要排除 setuptools——pkg_resources 运行时钩子依赖其内置 jaraco
        "torch",
        "torchvision",
        "torchaudio",
        "tensorboard",
        "numba",
        "llvmlite",
        "IPython",
        "ipykernel",
        "jupyter",
        "notebook",
        "nbconvert",
        "nbformat",
        "sphinx",
        "docutils",
        "pytest",
        "_pytest",
        "pip",
        # 体积精简：本软件运行时不使用的网络/图像/解析依赖
        # 注意：不可排除 certifi——pyproj 在导入时依赖它
        "requests",
        "pooch",
        "urllib3",
        "idna",
        "charset_normalizer",
        "httpcore",
        "httpx",
        "h11",
        "anyio",
        "sniffio",
        "lxml",
        "PIL._avif",
        "PIL.ImageQt",
        "PIL.ImageShow",
        "PIL.ImageTk",
        # OpenGeode 为可选引擎：其原生库在冻结环境下不稳定，打包版不内置，
        # 运行时引擎自动回退 Mock/OpenGeode（冻结版不预加载）。
        "opengeode",
        # vtk 元模块会 `from vtkmodules.* import *` 拉入全部 VTK 模块（约 200MB）；
        # PyVista 仅在 `except ImportError` 兜底分支引用它，正常运行使用 vtkmodules，
        # 故排除以仅保留实际用到的 VTK 模块。
        "vtk",
        # scipy 仅用于 cKDTree 加速；已提供 NumPy 兜底（spatial.py），
        # 冻结版剔除以减小体积（约 90MB）。
        "scipy",
    ],
    noarchive=False,
)

# 剔除软件 OpenGL 回退库（约 20MB）；目标环境使用硬件 OpenGL
a.binaries = TOC(
    [item for item in a.binaries if "opengl32sw" not in item[0].lower()]
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GeoModel3D",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="GeoModel3D",
)
