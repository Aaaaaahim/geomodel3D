"""性能与规模回归测试（NFR）。

以宽松的时间上限守护"灾难性性能回归"（例如误用逐单元 Python 循环），
并校验大规模网格的几何正确性。
"""
from __future__ import annotations

import time

import pytest

from geomodel3d.algorithms.benchmark import run_benchmark, synthetic_spec
from geomodel3d.algorithms.mesh_health import health_check
from geomodel3d.engine.mock import MockEngine


class _Ctx:
    def progress(self, percent: float, stage: str = "") -> None:
        return None

    def raise_if_cancelled(self) -> None:
        return None


def test_large_grid_volume_correctness():
    spec = synthetic_spec((60, 60, 12), size=600.0)
    mesh = MockEngine().build(spec, _Ctx())
    assert mesh.n_cells == 60 * 60 * 12
    assert mesh.total_volume() == pytest.approx(600.0 ** 3, rel=1e-6)
    assert mesh.cell_volumes().sum() == pytest.approx(600.0 ** 3, rel=1e-6)
    report = health_check(mesh)
    assert report.passed
    assert report.metrics["cells"] == mesh.n_cells


def test_benchmark_under_budget():
    start = time.perf_counter()
    results = run_benchmark((40, 40, 10))
    elapsed = time.perf_counter() - start
    assert results["cells"] == 40 * 40 * 10
    assert results["build"] < 5.0
    assert results["volumes"] < 5.0
    assert elapsed < 30.0
