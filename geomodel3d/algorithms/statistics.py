"""统计分析算法（M7）。

厚度分布、品位分布/累积曲线、断层产状统计与数据质量汇总。
headless 可测，不依赖 Qt；输出结构化字典供图表与报表复用。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np

from geomodel3d.domain.entities import Drillhole, Fault
from geomodel3d.domain.rules import QualityReport


def _finite(values: Sequence[float]) -> np.ndarray:
    """过滤 None / NaN / Inf。"""
    array = np.asarray([v for v in values if v is not None], dtype=np.float64)
    return array[np.isfinite(array)]


def summarize(values: Sequence[float]) -> Dict[str, float]:
    """基础统计量。"""
    array = _finite(values)
    if array.size == 0:
        return {"count": 0, "mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0,
                "p25": 0.0, "p50": 0.0, "p75": 0.0}
    return {
        "count": int(array.size),
        "mean": float(array.mean()),
        "std": float(array.std()),
        "min": float(array.min()),
        "max": float(array.max()),
        "p25": float(np.percentile(array, 25)),
        "p50": float(np.percentile(array, 50)),
        "p75": float(np.percentile(array, 75)),
    }


def histogram(values: Sequence[float], bins: int = 20) -> Dict[str, List[float]]:
    """直方图（返回分箱中心与计数）。"""
    array = _finite(values)
    if array.size == 0:
        return {"centers": [], "counts": []}
    counts, edges = np.histogram(array, bins=bins)
    centers = ((edges[:-1] + edges[1:]) / 2.0).tolist()
    return {"centers": centers, "counts": counts.astype(int).tolist()}


def cumulative_curve(values: Sequence[float]) -> Dict[str, List[float]]:
    """累积分布曲线。"""
    array = np.sort(_finite(values))
    if array.size == 0:
        return {"values": [], "percent": []}
    percent = (np.arange(1, array.size + 1) / array.size * 100.0).tolist()
    return {"values": array.tolist(), "percent": percent}


def thickness_statistics(
    drillholes: Sequence[Drillhole],
    layer_codes: Optional[Iterable[str]] = None,
    *,
    bins: int = 20,
) -> Dict[str, Dict]:
    """按地层统计钻孔分层厚度。"""
    wanted = set(layer_codes) if layer_codes else None
    grouped: Dict[str, List[float]] = {}
    for hole in drillholes:
        for interval in hole.intervals:
            if wanted and interval.layer_code not in wanted:
                continue
            thickness = interval.thickness
            if thickness is None:
                thickness = interval.depth_bottom - interval.depth_top
            grouped.setdefault(interval.layer_code, []).append(float(thickness))
    return {
        code: {"summary": summarize(values), "histogram": histogram(values, bins)}
        for code, values in sorted(grouped.items())
    }


def grade_statistics(
    drillholes: Sequence[Drillhole],
    layer_codes: Optional[Iterable[str]] = None,
    *,
    bins: int = 20,
) -> Dict:
    """品位分布与累积曲线。"""
    wanted = set(layer_codes) if layer_codes else None
    values: List[float] = []
    unit = "%"
    for hole in drillholes:
        for interval in hole.intervals:
            if wanted and interval.layer_code not in wanted:
                continue
            if interval.grade is not None:
                values.append(float(interval.grade))
                unit = interval.grade_unit or unit
    return {
        "unit": unit,
        "summary": summarize(values),
        "histogram": histogram(values, bins),
        "cumulative": cumulative_curve(values),
    }


def fault_statistics(faults: Sequence[Fault], *, rose_bin: float = 10.0) -> Dict:
    """断层产状玫瑰图、落差与长度-深度关系统计。"""
    if not faults:
        return {
            "count": 0,
            "rose": {"centers": [], "counts": []},
            "dip": summarize([]),
            "throw": summarize([]),
            "length_depth": [],
        }
    strikes = [float(fault.params.strike) for fault in faults]
    dips = [float(fault.params.dip) for fault in faults]
    throws = [float(fault.params.throw_v) for fault in faults]

    edges = np.arange(0.0, 360.0 + rose_bin, rose_bin)
    counts, _ = np.histogram(strikes, bins=edges)
    centers = ((edges[:-1] + edges[1:]) / 2.0).tolist()
    length_depth = [
        [float(fault.params.length), float(fault.params.depth_of_influence)] for fault in faults
    ]
    return {
        "count": len(faults),
        "rose": {"centers": centers, "counts": counts.astype(int).tolist()},
        "dip": summarize(dips),
        "throw": summarize(throws),
        "length_depth": length_depth,
    }


def quality_statistics(report: QualityReport) -> Dict:
    """数据质量报告汇总。"""
    by_code: Dict[str, int] = {}
    for issue in report.issues:
        by_code[issue.code] = by_code.get(issue.code, 0) + 1
    return {
        "summary": report.summary(),
        "by_code": by_code,
        "metrics": report.metrics,
    }


def mesh_layer_volumes(mesh, layer_codes: Optional[Iterable[str]] = None) -> Dict[str, float]:
    """按地层统计模型体积。"""
    from geomodel3d.algorithms.reserve import _cell_layer_codes

    codes = _cell_layer_codes(mesh)
    volumes = mesh.cell_volumes()
    wanted = set(layer_codes) if layer_codes else None
    result: Dict[str, float] = {}
    for index, code in enumerate(codes):
        if wanted and code not in wanted:
            continue
        result[code] = result.get(code, 0.0) + float(volumes[index])
    return result


__all__ = [
    "summarize",
    "histogram",
    "cumulative_curve",
    "thickness_statistics",
    "grade_statistics",
    "fault_statistics",
    "quality_statistics",
    "mesh_layer_volumes",
]
