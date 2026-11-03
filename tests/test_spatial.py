"""空间索引工具测试（含无 scipy 的 NumPy 兜底路径）。"""
from __future__ import annotations

import numpy as np
import pytest

from geomodel3d.algorithms import spatial


@pytest.fixture()
def no_scipy(monkeypatch):
    monkeypatch.setattr(spatial, "_load_kdtree", lambda: None)


def test_knn_indices_matches_bruteforce(no_scipy):
    rng = np.random.default_rng(1)
    points = rng.random((50, 3))
    queries = rng.random((10, 3))
    idx = spatial.knn_indices(points, queries, 4)
    assert idx.shape == (10, 4)
    for row, query in enumerate(queries):
        expected = np.argsort(((points - query) ** 2).sum(axis=1))[:4]
        assert set(idx[row].tolist()) == set(expected.tolist())


def test_nearest_distances(no_scipy):
    points = np.array([[0, 0, 0], [10, 0, 0]], dtype=float)
    queries = np.array([[1, 0, 0], [9, 0, 0]], dtype=float)
    distances = spatial.nearest_distances(points, queries)
    assert distances.tolist() == [1.0, 1.0]


def test_pairs_within_tolerance(no_scipy):
    points = np.array(
        [[0, 0, 0], [1e-9, 0, 0], [5, 5, 5], [5, 5, 5 + 1e-9]], dtype=float
    )
    pairs = spatial.pairs_within_tolerance(points, 1e-6)
    assert (0, 1) in pairs
    assert (2, 3) in pairs
    assert (0, 2) not in pairs


def test_smooth_values(no_scipy):
    points = np.array([[0, 0, 0], [0.1, 0, 0], [0.2, 0, 0], [0.3, 0, 0]], dtype=float)
    values = np.array([1.0, 1.0, 1.0, 100.0])
    smoothed = spatial.smooth_values(points, values, smooth=5.0)
    assert smoothed[3] < 100.0
    assert smoothed[0] > 1.0


def test_smooth_values_anisotropic(no_scipy):
    points = np.array([[0, 0, 0], [0, 0, 1.0], [5, 0, 0]], dtype=float)
    values = np.array([1.0, 100.0, 1.0])
    isotropic = spatial.smooth_values(points, values, smooth=1.0, anisotropy=1.0)
    anisotropic = spatial.smooth_values(points, values, smooth=1.0, anisotropy=0.1)
    # 降低 Z 权重后，垂向高值影响减弱
    assert anisotropic[0] <= isotropic[0]
