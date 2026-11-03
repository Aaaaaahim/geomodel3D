"""pytest 全局配置：无显示环境下禁用 VTK 视口。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("GEOMODEL3D_NO_VTK", "1")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from geomodel3d.config.schema import (  # noqa: E402
    Bounds,
    InterpConfig,
    ProjectConfig,
    ProjectMeta,
    StratigraphyEntry,
)
from geomodel3d.domain.entities import Drillhole, DrillholeInterval, Fault, FaultParams  # noqa: E402
from geomodel3d.domain.project import Project  # noqa: E402
from geomodel3d.engine import EngineRegistry, ModelingSpec  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def stratigraphy():
    return [
        StratigraphyEntry(layer_code="A", name="上覆层", order=0, color="#D9CBB8"),
        StratigraphyEntry(layer_code="B", name="矿层", order=1, color="#7FA8C9"),
        StratigraphyEntry(layer_code="C", name="基底", order=2, color="#6B7A8D"),
    ]


@pytest.fixture()
def drillholes():
    return [
        Drillhole(
            hole_id="H1",
            x=0.0,
            y=0.0,
            collar_elev=100.0,
            total_depth=80.0,
            intervals=[
                DrillholeInterval(layer_code="A", depth_top=0.0, depth_bottom=20.0, grade=1.2),
                DrillholeInterval(layer_code="B", depth_top=20.0, depth_bottom=50.0, grade=2.4),
                DrillholeInterval(layer_code="C", depth_top=50.0, depth_bottom=80.0),
            ],
        ),
        Drillhole(
            hole_id="H2",
            x=200.0,
            y=200.0,
            collar_elev=120.0,
            total_depth=80.0,
            intervals=[
                DrillholeInterval(layer_code="A", depth_top=0.0, depth_bottom=25.0, grade=0.8),
                DrillholeInterval(layer_code="B", depth_top=25.0, depth_bottom=60.0, grade=1.6),
                DrillholeInterval(layer_code="C", depth_top=60.0, depth_bottom=80.0),
            ],
        ),
    ]


@pytest.fixture()
def project(stratigraphy, drillholes):
    config = ProjectConfig(
        project=ProjectMeta(id="test", name="demo", bounds=Bounds(min=[0, 0, 0], max=[200, 200, 200])),
        stratigraphy=stratigraphy,
    )
    return Project(config, drillholes=drillholes)


@pytest.fixture()
def modeling_spec(project):
    return ModelingSpec(
        bounds=project.config.project.bounds,
        stratigraphy=project.config.stratigraphy,
        drillholes=project.drillholes,
        resolution=(8, 8, 6),
        interp=InterpConfig(),
        engine="mock",
    )


class _ProgressCtx:
    def progress(self, percent: float, stage: str = "") -> None:
        return None

    def raise_if_cancelled(self) -> None:
        return None


@pytest.fixture()
def ctx():
    return _ProgressCtx()


@pytest.fixture()
def registry():
    return EngineRegistry()


@pytest.fixture()
def sample_vertices():
    return np.array(
        [
            [0, 0, 0],
            [1, 0, 0],
            [1, 1, 0],
            [0, 1, 0],
            [0, 0, 1],
            [1, 0, 1],
            [1, 1, 1],
            [0, 1, 1],
        ],
        dtype=float,
    )
