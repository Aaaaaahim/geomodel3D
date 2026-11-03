"""性能基准脚本（6.6 / NFR）。

生成规则网格模型并对建模、体积、体检、剖面、储量计时，用于回归监控。

用法::

    python -m geomodel3d.algorithms.benchmark
    python -m geomodel3d.algorithms.benchmark --resolution 100 100 20
"""
from __future__ import annotations

import argparse
import time
from typing import Dict, List, Optional, Sequence

from geomodel3d.config.schema import Bounds, InterpConfig, StratigraphyEntry
from geomodel3d.domain.reserve import ReserveMetrics
from geomodel3d.engine.mock import MockEngine
from geomodel3d.engine.base import ModelingSpec


class _SilentContext:
    def progress(self, percent: float, stage: str = "") -> None:
        return None

    def raise_if_cancelled(self) -> None:
        return None


def synthetic_spec(resolution: Sequence[int], size: float = 1000.0) -> ModelingSpec:
    return ModelingSpec(
        bounds=Bounds(min=[0, 0, 0], max=[size, size, size]),
        stratigraphy=[
            StratigraphyEntry(layer_code="A", order=0, color="#D9CBB8"),
            StratigraphyEntry(layer_code="B", order=1, color="#7FA8C9"),
        ],
        resolution=tuple(resolution),
        interp=InterpConfig(),
    )


def run_benchmark(resolution: Sequence[int] = (100, 100, 20)) -> Dict[str, float]:
    """执行一次基准，返回各阶段耗时（秒）与单元数。"""
    from geomodel3d.algorithms.mesh_health import health_check
    from geomodel3d.algorithms.reserve import estimate_reserves
    from geomodel3d.algorithms.section import build_section, make_horizontal_section

    spec = synthetic_spec(resolution)
    ctx = _SilentContext()
    engine = MockEngine()
    results: Dict[str, float] = {}

    start = time.perf_counter()
    mesh = engine.build(spec, ctx)
    results["build"] = time.perf_counter() - start

    start = time.perf_counter()
    mesh.cell_volumes()
    results["volumes"] = time.perf_counter() - start

    start = time.perf_counter()
    health_check(mesh)
    results["health"] = time.perf_counter() - start

    plane = make_horizontal_section(spec.bounds.min[2] + 500.0, bounds=[spec.bounds.min, spec.bounds.max])
    start = time.perf_counter()
    build_section(mesh, plane)
    results["section"] = time.perf_counter() - start

    metrics = ReserveMetrics(body_layers=["B"], method="volume", density=2.6)
    start = time.perf_counter()
    estimate_reserves(mesh, metrics, interp=InterpConfig())
    results["reserve"] = time.perf_counter() - start

    results["cells"] = float(mesh.n_cells)
    results["total"] = sum(value for key, value in results.items() if key not in ("cells", "total"))
    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="geomodel3d-bench", description="三维地质建模性能基准")
    parser.add_argument("--resolution", nargs=3, type=int, default=[100, 100, 20], metavar=("NX", "NY", "NZ"))
    parser.add_argument("--max-total", type=float, default=0.0, help="总耗时上限（秒），超出返回非零")
    args = parser.parse_args(argv)

    results = run_benchmark(tuple(args.resolution))
    print(f"网格分辨率: {args.resolution} · 单元数: {int(results['cells']):,}")
    for stage in ("build", "volumes", "health", "section", "reserve", "total"):
        print(f"  {stage:>8}: {results[stage]:.3f} s")
    if args.max_total and results["total"] > args.max_total:
        print(f"超出预算: {results['total']:.3f}s > {args.max_total:.3f}s")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
