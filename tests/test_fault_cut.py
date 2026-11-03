"""VTK 断层几何切割预览测试。"""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pyvista")

from geomodel3d.algorithms.benchmark import synthetic_spec
from geomodel3d.algorithms.fault_cut import cut_mesh_by_fault, fault_plane
from geomodel3d.config.errors import GeoModelError
from geomodel3d.domain.entities import Fault, FaultParams
from geomodel3d.engine.mock import MockEngine


class _Ctx:
    def progress(self, percent, stage=""):
        return None

    def raise_if_cancelled(self):
        return None


def _mesh():
    return MockEngine().build(synthetic_spec((8, 8, 6)), _Ctx())


def _fault(y: float = 500.0, *, throw_v: float = 50.0, throw_h: float = 20.0) -> Fault:
    return Fault(
        fault_id="F1",
        trace=[[0.0, y, 500.0], [1000.0, y, 500.0]],
        params=FaultParams(
            length=1000,
            depth_of_influence=1000,
            dip=90,
            strike=90,
            throw_v=throw_v,
            throw_h=throw_h,
            buffer=100,
        ),
    )


def test_fault_plane_vertical_normal():
    normal, origin = fault_plane(_fault())
    assert np.allclose(normal, [0.0, 1.0, 0.0])
    assert np.allclose(origin, [500.0, 500.0, 500.0])


def test_cut_mesh_preserves_volume_and_marks_preview():
    mesh = _mesh()
    cut = cut_mesh_by_fault(mesh, _fault())
    from geomodel3d.algorithms.mesh_health import health_check

    assert cut.n_cells > 0
    assert cut.meta["engine"] == "vtk_fault_cut"
    assert cut.meta["fault_cut"]["watertight"] is True
    assert int((cut.cell_volumes() <= 1e-12).sum()) == 0
    assert health_check(cut).passed
    assert cut.total_volume() == pytest.approx(mesh.total_volume(), rel=1e-9)
    assert cut.face_groups
    assert sum(len(values) for values in cut.face_groups.values()) == cut.n_cells


def test_cut_outside_model_fails_loudly():
    with pytest.raises(GeoModelError) as excinfo:
        cut_mesh_by_fault(_mesh(), _fault(y=5000.0))
    assert excinfo.value.code.value == "MDL-CUT-001"
