"""OpenGeode 引擎适配器测试（M3，仅在依赖可用时运行）。"""
from __future__ import annotations

import numpy as np
import pytest

from geomodel3d.config.errors import GeoModelError
from geomodel3d.config.schema import Bounds, InterpConfig, StratigraphyEntry
from geomodel3d.domain.entities import Drillhole, DrillholeInterval, Fault, FaultParams
from geomodel3d.engine import EngineRegistry, ModelingSpec
from geomodel3d.engine.opengeode_engine import (
    OpenGeodeEngine,
    from_hybrid_solid,
    opengeode_available,
    save_native,
    to_hybrid_solid,
)
from geomodel3d.engine.umesh import (
    VTK_PYRAMID,
    VTK_TETRA,
    VTK_TRIANGLE,
    VTK_WEDGE,
    UMesh,
)

pytestmark = pytest.mark.skipif(not opengeode_available(), reason="OpenGeode 未安装")


class _Ctx:
    def progress(self, percent: float, stage: str = "") -> None:
        return None

    def raise_if_cancelled(self) -> None:
        return None


def _spec(resolution=(8, 8, 6)):
    holes = [
        Drillhole(
            hole_id="H1", x=0, y=0, collar_elev=100, total_depth=80,
            intervals=[
                DrillholeInterval(layer_code="A", depth_top=0, depth_bottom=20),
                DrillholeInterval(layer_code="B", depth_top=20, depth_bottom=50),
            ],
        )
    ]
    return ModelingSpec(
        bounds=Bounds(min=[0, 0, 0], max=[200, 200, 200]),
        stratigraphy=[
            StratigraphyEntry(layer_code="A", order=0),
            StratigraphyEntry(layer_code="B", order=1),
        ],
        drillholes=holes,
        resolution=resolution,
        interp=InterpConfig(),
    )


def test_engine_available_and_builds_solid():
    mesh = OpenGeodeEngine().build(_spec(), _Ctx())
    assert mesh.n_cells == 8 * 8 * 6
    assert mesh.meta["engine"] == "opengeode"
    assert mesh.meta["opengeode"]["nb_polyhedra"] == mesh.n_cells
    assert hasattr(mesh, "opengeode_solid")


def test_native_roundtrip(tmp_path):
    mesh = OpenGeodeEngine().build(_spec(), _Ctx())
    path = save_native(mesh, tmp_path / "solid")
    assert path.exists() and path.stat().st_size > 0

    import opengeode as og

    restored = from_hybrid_solid(og.load_hybrid_solid3D(str(path)))
    assert restored.n_cells == mesh.n_cells
    assert restored.total_volume() == pytest.approx(mesh.total_volume(), rel=1e-9)


def test_registry_prefers_opengeode_with_faults():
    spec = _spec()
    spec.faults = [
        Fault(
            fault_id="F1",
            trace=[[0, 100, 0], [200, 100, 0]],
            params=FaultParams(length=200, depth_of_influence=200, dip=60, strike=90, throw_v=20),
        )
    ]
    engine = EngineRegistry().resolve(spec)
    assert engine.name == "opengeode"


def test_to_hybrid_solid_attribute():
    mesh = OpenGeodeEngine().build(_spec((4, 4, 3)), _Ctx())
    solid = to_hybrid_solid(mesh)
    assert solid.nb_polyhedra() == mesh.n_cells
    assert "layer" in solid.polyhedron_attribute_manager().attribute_names()


@pytest.mark.parametrize(
    ("vertices", "cell", "vtk_type"),
    [
        (
            np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float),
            [0, 1, 2, 3],
            VTK_TETRA,
        ),
        (
            np.array(
                [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [0, 1, 1]],
                dtype=float,
            ),
            [0, 1, 2, 3, 4, 5],
            VTK_WEDGE,
        ),
        (
            np.array(
                [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0.5, 0.5, 1]],
                dtype=float,
            ),
            [0, 1, 2, 3, 4],
            VTK_PYRAMID,
        ),
    ],
)
def test_to_hybrid_solid_supports_mixed_cell_types(vertices, cell, vtk_type):
    mesh = UMesh(
        vertices=vertices,
        cells=[np.asarray(cell, dtype=np.int64)],
        cell_types=np.array([vtk_type]),
    )
    solid = to_hybrid_solid(mesh)
    assert solid.nb_polyhedra() == 1

    restored = from_hybrid_solid(solid)
    assert restored.n_cells == 1
    assert restored.total_volume() == pytest.approx(mesh.total_volume(), rel=1e-9)


def test_to_hybrid_solid_rejects_unsupported_cell():
    mesh = UMesh(
        vertices=np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float),
        cells=[np.arange(3)],
        cell_types=np.array([VTK_TRIANGLE]),
    )
    with pytest.raises(GeoModelError) as excinfo:
        to_hybrid_solid(mesh)
    assert excinfo.value.code.value == "MDL-ENG-001"
