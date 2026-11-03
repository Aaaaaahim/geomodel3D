"""协同克里金 / 带漂移克里金测试（6.2）。"""
from __future__ import annotations

import numpy as np
import pytest

from geomodel3d.algorithms.interpolation import cokriging, kriging, kriging_with_drift


def _samples():
    rng = np.random.default_rng(7)
    xy = rng.random((8, 2)) * 100.0
    z = 5.0 + 0.3 * xy[:, 0] + 0.2 * xy[:, 1]
    points = np.column_stack([xy, np.zeros(8)])
    return points, z


def test_trend_weight_zero_reduces_to_kriging():
    points, values = _samples()
    query = np.array([[30.0, 40.0, 0.0], [70.0, 10.0, 0.0]])
    ok = kriging(points, values, query, k=8)
    ked = kriging_with_drift(points, values, query, values, values, trend_weight=0.0, k=8)
    assert np.allclose(ok, ked, atol=1e-6)


def test_kriging_with_drift_exact_at_samples():
    points, values = _samples()
    est = kriging_with_drift(points, values, points, values, values, trend_weight=1.0, k=8)
    assert np.allclose(est, values, atol=1e-6)


def test_cokriging_exact_and_finite():
    points, values = _samples()
    # 协变量：比主变量更密的平面趋势
    rng = np.random.default_rng(3)
    secondary_xy = rng.random((200, 2)) * 100.0
    secondary = 5.0 + 0.3 * secondary_xy[:, 0] + 0.2 * secondary_xy[:, 1]
    est = cokriging(points, values, points, secondary_xy, secondary, trend_weight=0.6)
    assert np.all(np.isfinite(est))
    assert np.allclose(est, values, atol=1e-6)

    query = np.array([[50.0, 50.0, 0.0]])
    out = cokriging(points, values, query, secondary_xy, secondary, trend_weight=0.6)
    assert 5.0 < out[0] < 35.0
