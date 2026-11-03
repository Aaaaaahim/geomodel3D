"""应用入口（9.1 app 层）。

创建 QApplication、装配依赖、应用主题并显示主窗口；安装全局异常钩子。

设置环境变量 ``GEOMODEL3D_SELF_TEST=1`` 可执行无界面自检：装配依赖、
创建主窗口、处理事件后立即退出（返回 0），用于打包产物冒烟验证。
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

# 支持直接运行本文件（python geomodel3d/app/main.py）：把工程根加入 sys.path
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from geomodel3d.app.context import AppContext
from geomodel3d.app.theme import apply_theme
from geomodel3d.config.defaults import APP_NAME, APP_ORG
from geomodel3d.ui.main_window import MainWindow

logger = logging.getLogger(__name__)


def build_application(argv: Optional[List[str]] = None) -> QApplication:
    """创建并配置 QApplication。"""
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)
    app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, False)
    return app


def main(argv: Optional[List[str]] = None) -> int:
    """程序入口，返回退出码。"""
    app = build_application(argv)
    context = AppContext()
    apply_theme(app, context.settings.theme)

    def _excepthook(exc_type, exc_value, exc_tb) -> None:
        error = context.exceptions.report(exc_value, publish=False)
        from geomodel3d.app.crash_dump import write_minidump
        from geomodel3d.ui.dialogs.error_dialog import ErrorDialog

        detail = context.exceptions.traceback_text(exc_value)
        dump = write_minidump(context.crash_dir())
        if dump is not None:
            detail += f"\n\n崩溃转储: {dump}"
        ErrorDialog(
            error.message,
            code=error.code.value,
            suggestion=error.suggestion,
            detail=detail,
        ).exec()

    sys.excepthook = _excepthook

    window = MainWindow(context)
    window.show()

    if os.environ.get("GEOMODEL3D_SELF_TEST"):
        app.processEvents()
        try:
            _self_test(context)
        except Exception as exc:  # noqa: BLE001
            print(f"SELF_TEST_FAILED: {exc}")
            return 1
        print("SELF_TEST_OK")
        context.shutdown()
        return 0
    return app.exec()


def _self_test(context: AppContext) -> None:
    """无界面自检：导入钻孔（自动推导地层）→ 建模 → 储量 → 剖面 → VTU 导出。"""
    from geomodel3d.config.schema import Bounds, CRSConfig
    from geomodel3d.dataio.crs import CRSService
    from geomodel3d.domain.reserve import ReserveMetrics
    from geomodel3d.services.modeling_service import spec_from_project

    project = context.projects.new_project(
        "self_test",
        crs_epsg=32650,
        bounds=Bounds(min=[0, 0, 0], max=[200, 200, 200]),
    )
    with tempfile.TemporaryDirectory() as tmp:
        csv = Path(tmp) / "boreholes.csv"
        csv.write_text(
            "孔号,东坐标,北坐标,孔口高程,层顶深,层底深,地层代码,品位\n"
            "H1,0,0,100,0,20,A,1.2\nH1,0,0,100,20,50,B,2.4\n"
            "H2,180,180,120,0,25,A,0.9\nH2,180,180,120,25,60,B,1.6\n",
            encoding="utf-8-sig",
        )
        context.data.import_boreholes(csv)
        assert project.config.stratigraphy, "导入钻孔后未推导地层字典"

        spec = spec_from_project(project, engine="mock", resolution=(6, 6, 4))
        context.modeling.create_run(spec, async_run=False)
        assert project.has_model(), "建模失败"

        # CRS 变换（验证 pyproj 及其依赖已随包）
        import numpy as np

        transformed = CRSService().transform_points(
            np.array([[0.0, 0.0, 0.0]]), CRSConfig(epsg=4326), CRSConfig(epsg=32650)
        )
        assert transformed.shape == (1, 3)

        reserve_result = context.reserve.plan(
            ReserveMetrics(body_layers=["B"], method="volume", density=2.6)
        )
        context.reserve.export_report(reserve_result, Path(tmp) / "reserve.xlsx")

        section_result = context.section.create_horizontal(60)
        context.section.export(section_result, Path(tmp) / "section.shp", "shp")

        target = Path(tmp) / "self_test.vtu"
        context.exporter.export_mesh(target)
        assert target.exists() and target.stat().st_size > 0, "VTU 导出失败"


if __name__ == "__main__":
    raise SystemExit(main())

