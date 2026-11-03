"""统一模型 UMesh 测试。"""
from __future__ import annotations

import numpy as np
import pytest

from geomodel3d.engine.umesh import VTK_HEXAHEDRON, VTK_PYRAMID, VTK_TETRA, UMesh


def test_unit_cube_volume(sample_vertices):
    mesh = UMesh(
        vertices=sample_vertices,
        cells=[np.arange(8)],
        cell_types=np.array([VTK_HEXAHEDRON]),
    )
    assert mesh.n_points == 8
    assert mesh.n_cells == 1
    assert mesh.total_volume() == pytest.approx(1.0)
    assert mesh.bounds() == [0, 1, 0, 1, 0, 1]


def test_tetra_volume():
    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    mesh = UMesh(vertices=vertices, cells=[np.arange(4)], cell_types=np.array([VTK_TETRA]))
    assert mesh.total_volume() == pytest.approx(1.0 / 6.0)


def test_pyramid_volume_and_health():
    vertices = np.array(
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0.5, 0.5, 1]], dtype=float
    )
    mesh = UMesh(
        vertices=vertices,
        cells=[np.arange(5)],
        cell_types=np.array([VTK_PYRAMID]),
    )
    assert mesh.total_volume() == pytest.approx(1.0 / 3.0)

    from geomodel3d.algorithms.mesh_health import health_check

    report = health_check(mesh)
    assert report.metrics["cells"] == 1.0
    assert report.passed


def test_cell_extents_mixed_cell_shapes():
    """非均匀单元长度应走 NumPy 兜底路径，并兼容 NumPy 2 的 ptp 写法。"""
    vertices = np.array(
        [
            [0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1],
            [10, 0, 0], [11, 0, 0], [11, 1, 0], [10, 1, 0],
            [10, 0, 1], [11, 0, 1], [11, 1, 1], [10, 1, 1],
        ],
        dtype=float,
    )
    mesh = UMesh(
        vertices=vertices,
        cells=[np.arange(4), 4 + np.arange(8)],
        cell_types=np.array([VTK_TETRA, VTK_HEXAHEDRON]),
    )
    assert mesh.cell_extents(axis=0).tolist() == [1.0, 1.0]
    assert mesh.cell_extents(axis=2).tolist() == [1.0, 1.0]


def test_content_hash_stable(sample_vertices):
    mesh = UMesh(vertices=sample_vertices, cells=[np.arange(8)], cell_types=np.array([VTK_HEXAHEDRON]))
    assert mesh.content_hash() == mesh.copy().content_hash()


def test_invalid_vertices():
    with pytest.raises(ValueError):
        UMesh(vertices=np.zeros((3, 2)), cells=[], cell_types=np.array([]))


def test_cell_attr_length_validation(sample_vertices):
    with pytest.raises(ValueError):
        UMesh(
            vertices=sample_vertices,
            cells=[np.arange(8)],
            cell_types=np.array([VTK_HEXAHEDRON]),
            cell_attr={"layer": np.array([1, 2])},
        )


def test_pyvista_roundtrip(sample_vertices):
    pytest.importorskip("pyvista")
    mesh = UMesh(
        vertices=sample_vertices,
        cells=[np.arange(8)],
        cell_types=np.array([VTK_HEXAHEDRON]),
        cell_attr={"layer": np.array([0])},
    )
    restored = UMesh.from_pyvista(mesh.to_pyvista())
    assert restored.n_cells == mesh.n_cells
    assert restored.total_volume() == pytest.approx(mesh.total_volume())
    assert "layer" in restored.cell_attr


def test_health_check_wedge_mesh():
    """楔体单元面片顶点数不一致，体检应走标量兜底而不是崩溃。"""
    from geomodel3d.algorithms.mesh_health import health_check
    from geomodel3d.engine.umesh import VTK_WEDGE

    vertices = np.array(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [0, 1, 1]], dtype=float
    )
    mesh = UMesh(vertices=vertices, cells=[np.arange(6)], cell_types=np.array([VTK_WEDGE]))
    report = health_check(mesh)
    assert report.metrics["cells"] == 1.0
