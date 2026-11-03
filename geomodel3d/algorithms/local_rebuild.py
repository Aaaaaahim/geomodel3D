"""局部重建模（4.4 / BR-03）。

只对断层影响域内的单元重算地层归属，影响域外保持不变；在影响域边界
设置平滑过渡带（smoothstep 权重），使新/旧断层落差渐变，避免接缝跳变。
headless 可测，不依赖 Qt。
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

import numpy as np

from geomodel3d.algorithms.geometry import region_contains


def rebuild_local_layered(base: "UMesh", spec: "ModelingSpec", ctx) -> "UMesh":
    """在既有模型上做局部重建模。

    ``spec.region`` 为影响域 ``[x0,y0,z0,x1,y1,z1]``；仅域内（含过渡带）
    单元被重算，其余单元原样保留。
    """
    from geomodel3d.engine.layered import (
        _assign_layers,
        _fault_adjusted_xy,
        _fault_side_mask,
        _interface_surfaces,
    )

    region = spec.region
    if region is None:
        raise ValueError("局部重建模需要 spec.region 影响域")

    ctx.progress(10, "计算影响域")
    centers = base.cell_centers()
    band = float(spec.extra.get("transition_band") or _default_band(region))
    expanded = _expand(region, band)
    affected = region_contains(expanded, centers)
    count = int(affected.sum())
    if count == 0:
        return base.copy()

    layers = sorted(spec.stratigraphy, key=lambda entry: entry.order)
    n_layers = max(len(layers), 1)
    weights = _transition_weight(centers[affected], region, band)

    ctx.progress(35, "重算影响域界面")
    z = centers[affected][:, 2].copy()
    fault_mask = np.zeros(count, dtype=bool)
    edited_id = spec.extra.get("fault_id")
    old_throw = float(spec.extra.get("old_throw", 0.0))
    old_throw_h = float(spec.extra.get("old_throw_h", 0.0))

    xy = _fault_adjusted_xy(
        centers[affected],
        spec.faults,
        edited_id=edited_id,
        old_throw_h=old_throw_h,
        blend=weights,
    )
    boundaries = _interface_surfaces(spec, layers, xy)

    for fault in spec.faults:
        if fault.status != "active":
            continue
        side = _fault_side_mask(centers[affected], fault)
        if fault.params.throw_h > 0:
            fault_mask |= side
        if fault.params.throw_v <= 0:
            continue
        new_throw = float(fault.params.throw_v)
        blend = (
            weights * new_throw + (1.0 - weights) * old_throw
            if fault.fault_id == edited_id
            else np.full(count, new_throw)
        )
        z[side] -= blend[side]
        fault_mask |= side

    ctx.progress(70, "写回受影响单元")
    layer_index = _assign_layers(z, boundaries, n_layers)

    new_mesh = base.copy()
    merged = np.asarray(new_mesh.cell_attr["layer"], dtype=np.int32).copy()
    merged[affected] = layer_index
    new_mesh.cell_attr["layer"] = merged
    if "fault_affected" in new_mesh.cell_attr:
        flags = np.asarray(new_mesh.cell_attr["fault_affected"], dtype=np.int8).copy()
        flags[affected] = fault_mask.astype(np.int8)
        new_mesh.cell_attr["fault_affected"] = flags
    new_mesh.face_groups = _face_groups(merged, layers)
    new_mesh.version = base.version + 1
    new_mesh.meta = {
        **base.meta,
        "local_rebuild": {
            "region": [float(v) for v in region],
            "affected": count,
            "total": int(base.n_cells),
            "transition_band": band,
            "fault_id": edited_id,
        },
    }
    ctx.progress(100, "完成")
    return new_mesh


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #


def _default_band(region: Sequence[float]) -> float:
    span = min(abs(region[3] - region[0]), abs(region[4] - region[1]))
    return max(span * 0.2, 1.0)


def _expand(region: Sequence[float], band: float) -> List[float]:
    return [
        region[0] - band,
        region[1] - band,
        region[2] - band,
        region[3] + band,
        region[4] + band,
        region[5] + band,
    ]


def _transition_weight(points: np.ndarray, region: Sequence[float], band: float) -> np.ndarray:
    """边界处权重 0、核心处权重 1 的 smoothstep 过渡（含竖向距离）。"""
    if band <= 0:
        return np.ones(points.shape[0])
    dx = np.minimum(points[:, 0] - region[0], region[3] - points[:, 0])
    dy = np.minimum(points[:, 1] - region[1], region[4] - points[:, 1])
    dz = np.minimum(points[:, 2] - region[2], region[5] - points[:, 2])
    distance = np.minimum(np.minimum(dx, dy), dz)
    ratio = np.clip(distance / band, 0.0, 1.0)
    return ratio * ratio * (3.0 - 2.0 * ratio)


def _face_groups(layer_index: np.ndarray, layers: Sequence[Any]) -> Dict[str, List[int]]:
    # layers 已按 order 排序，层序索引即位置（不依赖 order 连续性）
    groups: Dict[str, List[int]] = {}
    for position, entry in enumerate(layers):
        groups[entry.layer_code] = np.nonzero(layer_index == position)[0].tolist()
    return groups


__all__ = ["rebuild_local_layered"]
