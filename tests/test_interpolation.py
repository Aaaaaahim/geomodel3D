"""插值算法测试。"""
from __future__ import annotations

import numpy as np
import pytest

from geomodel3d.algorithms.interpolation import (
    dsi,
    ensure_interp_method,
    idw,
    kriging,
    recommended_k,
)
from geomodel3d.config.errors import GeoModelError


def test_idw_exact_at_samples():
    points = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], dtype=float)
    values = np.array([1.0, 2.0, 3.0, 4.0])
    result = idw(points, values, points)
    assert np.allclose(result, values)


def test_idw_interpolates_between():
    points = np.array([[0, 0, 0], [10, 0, 0]], dtype=float)
    values = np.array([0.0, 10.0])
    mid = idw(points, values, np.array([[5.0, 0.0, 0.0]]))
    assert 4.0 < mid[0] < 6.0


def test_idw_neighbor_limit():
    points = np.random.default_rng(0).random((50, 3))
    values = np.arange(50.0)
    query = np.random.default_rng(1).random((5, 3))
    result = idw(points, values, query, k=8)
    assert result.shape == (5,)
    assert np.all(np.isfinite(result))


def test_kriging_finite_and_exact():
    points = np.array([[0, 0, 0], [5, 0, 0], [0, 5, 0], [5, 5, 0], [2, 2, 0]], dtype=float)
    values = np.array([1.0, 2.0, 1.5, 2.5, 2.0])
    result = kriging(points, values, points)
    assert np.all(np.isfinite(result))
    assert np.allclose(result, values, atol=1e-6)


def test_ensure_interp_method_accepts_implemented():
    for method in ("idw", "kriging", "cokriging", "dsi"):
        assert ensure_interp_method(method) == method


def test_ensure_interp_method_rejects_unimplemented():
    with pytest.raises(GeoModelError) as excinfo:
        ensure_interp_method("gempy")
    assert excinfo.value.code.value == "MDL-IPL-002"


def test_dsi_honors_samples_and_stays_finite():
    points = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], dtype=float)
    values = np.array([0.0, 1.0, 0.5, 1.5])
    query = np.vstack([points, [[0.5, 0.5, 0.0]]])
    result = dsi(points, values, query, k=3, iterations=10)
    assert np.all(np.isfinite(result))
    assert np.allclose(result[:4], values, atol=1e-9)
    assert result.shape == (5,)


def test_recommended_k_batches_small_samples():
    # 小样本用全量（触发单次批量求解）
    assert recommended_k(25, 24) == 25
    assert recommended_k(256, 16) == 256
    # 大样本回退局部邻域
    assert recommended_k(5000, 24) == 24
