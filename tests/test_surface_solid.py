"""层实体几何测试（顶/底曲面 -> 封闭实体）。"""
from __future__ import annotations

import numpy as np
import pytest

from geomodel3d.algorithms.surface_solid import layer_solid_geometry


def test_layer_solid_geometry_unit_cube():
    xs = np.array([0.0, 1.0])
    ys = np.array([0.0, 1.0])
    top = np.array([[1.0, 1.0], [1.0, 1.0]])
    bottom = np.array([[0.0, 0.0], [0.0, 0.0]])
    vertices, faces = layer_solid_geometry(xs, ys, top, bottom)
    assert vertices.shape == (8, 3)
    assert faces.size > 0

    import pyvista as pv

    solid = pv.PolyData(vertices, faces)
    assert solid.volume == pytest.approx(1.0, rel=1e-6)
    edges = solid.extract_feature_edges(
        boundary_edges=True, non_manifold_edges=False,
        feature_edges=False, manifold_edges=False,
    )
    assert edges.n_cells == 0  # 封闭、无自由边


def test_layer_solid_geometry_nan_all_skipped():
    xs = np.array([0.0, 1.0])
    ys = np.array([0.0, 1.0])
    nan = np.full((2, 2), np.nan)
    _, faces = layer_solid_geometry(xs, ys, nan, nan)
    assert faces.size == 0


def test_layer_solid_geometry_degenerate_grid():
    _, faces = layer_solid_geometry(np.array([0.0]), np.array([0.0]), np.zeros((1, 1)), np.zeros((1, 1)))
    assert faces.size == 0
