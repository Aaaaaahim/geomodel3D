"""几何算法测试（BR-03 / 6.3）。"""
from __future__ import annotations

import numpy as np

from geomodel3d.algorithms.geometry import (
    fault_impact_region,
    region_contains,
    region_intersection,
)
from geomodel3d.domain.entities import Fault, FaultParams


def _fault():
    return Fault(
        fault_id="F1",
        trace=[[0, 100, 0], [200, 100, 0]],
        params=FaultParams(length=200, depth_of_influence=150, dip=60, strike=90, buffer=20),
    )


def test_impact_region_within_bounds():
    region = fault_impact_region(_fault(), [[0, 0, 0], [200, 200, 200]])
    assert region[0] >= 0 and region[3] <= 200
    assert region[1] >= 0 and region[4] <= 200
    assert region[5] == 200
    assert region[2] == 50


def test_region_intersection():
    a = [0, 0, 0, 10, 10, 10]
    b = [5, 5, 5, 20, 20, 20]
    assert region_intersection(a, b) == [5, 5, 5, 10, 10, 10]
    assert region_intersection(a, [100, 100, 100, 110, 110, 110]) is None


def test_region_contains():
    region = [0, 0, 0, 10, 10, 10]
    points = np.array([[5, 5, 5], [20, 0, 0], [-1, 5, 5]], dtype=float)
    mask = region_contains(region, points)
    assert mask.tolist() == [True, False, False]
