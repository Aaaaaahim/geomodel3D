"""几何算法：断层影响域、区域裁剪等（BR-03 / 6.3）。"""
from __future__ import annotations

from typing import List, Optional, Sequence

import numpy as np

from geomodel3d.domain.entities import Fault


def fault_impact_region(
    fault: Fault,
    bounds: Sequence[Sequence[float]],
    *,
    surface_z: Optional[float] = None,
) -> List[float]:
    """计算断层影响域（轴对齐包围盒）。

    影响域 = 沿走向 ±L/2、横向缓冲 Δ 的走向带 ∩（自参考面向下 D 的竖向范围）
    ∩ 模型包围盒。

    Returns:
        ``[xmin, ymin, zmin, xmax, ymax, zmax]``
    """
    (bx0, by0, bz0), (bx1, by1, bz1) = bounds
    params = fault.params
    trace = np.asarray(fault.trace, dtype=np.float64)
    if trace.shape[0] > 0:
        center = trace[:, :2].mean(axis=0)
    else:
        center = np.array([(bx0 + bx1) / 2.0, (by0 + by1) / 2.0])

    strike = np.deg2rad(params.strike)
    # 走向约定：自正北顺时针，沿走向方向 = (sin, cos)；优先用迹线方向
    fallback_along = np.array([np.sin(strike), np.cos(strike)])
    if trace.shape[0] >= 2:
        delta = trace[-1, :2] - trace[0, :2]
        norm = float(np.linalg.norm(delta))
        along = delta / norm if norm > 1e-9 else fallback_along
    else:
        along = fallback_along
    across = np.array([-along[1], along[0]])
    half_len = params.length / 2.0
    half_width = max(params.buffer, 0.0)

    corners = []
    for s in (-1, 1):
        for w in (-1, 1):
            corner = center + s * half_len * along + w * half_width * across
            corners.append(corner)
    corners = np.asarray(corners)

    x0 = max(bx0, float(corners[:, 0].min()))
    x1 = min(bx1, float(corners[:, 0].max()))
    y0 = max(by0, float(corners[:, 1].min()))
    y1 = min(by1, float(corners[:, 1].max()))
    top = bz1 if surface_z is None else min(bz1, surface_z)
    z1 = top
    z0 = max(bz0, top - params.depth_of_influence)
    return [x0, y0, z0, x1, y1, z1]


def region_intersection(a: Sequence[float], b: Sequence[float]) -> Optional[List[float]]:
    """两个轴对齐包围盒求交，无交集返回 ``None``。"""
    out = [max(a[0], b[0]), max(a[1], b[1]), max(a[2], b[2]),
           min(a[3], b[3]), min(a[4], b[4]), min(a[5], b[5])]
    if out[0] > out[3] or out[1] > out[4] or out[2] > out[5]:
        return None
    return out


def region_contains(region: Sequence[float], points: np.ndarray) -> np.ndarray:
    """判断点是否落入包围盒，返回布尔掩码。"""
    pts = np.asarray(points, dtype=np.float64)
    return (
        (pts[:, 0] >= region[0])
        & (pts[:, 0] <= region[3])
        & (pts[:, 1] >= region[1])
        & (pts[:, 1] <= region[4])
        & (pts[:, 2] >= region[2])
        & (pts[:, 2] <= region[5])
    )


__all__ = ["fault_impact_region", "region_intersection", "region_contains"]
