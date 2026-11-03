"""分层体元建模核心（Mock 与 OpenGeode 适配器共用）。

以钻孔分层界面为约束（IDW/克里金插值），按层序把规则六面体单元分配给
各地层，并支持基于走向的断层落差位移。输出统一的 UMesh。
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

import numpy as np

from geomodel3d.algorithms.interpolation import (
    dsi,
    ensure_interp_method,
    idw,
    kriging,
    recommended_k,
)
from geomodel3d.config.errors import ErrorCode, GeoModelError
from geomodel3d.domain.entities import Drillhole, Fault, FaultSide
from geomodel3d.engine.base import ModelingSpec
from geomodel3d.engine.umesh import VTK_HEXAHEDRON, UMesh


def build_layered_mesh(spec: ModelingSpec, ctx, *, engine_name: str = "mock") -> UMesh:
    """构建分层六面体统一模型。"""
    started = time.perf_counter()
    ctx.progress(2, "准备网格")
    nx, ny, nz = (int(v) for v in spec.resolution)
    if min(nx, ny, nz) < 1:
        raise ValueError("网格分辨率必须为正整数")

    xs, ys, zs = _axes(spec.bounds, nx, ny, nz)
    vertices, cells = _voxel_grid(xs, ys, zs)
    x_mid, y_mid = _mid(xs), _mid(ys)
    centers = np.stack(
        np.meshgrid(x_mid, y_mid, _mid(zs), indexing="ij"), axis=-1
    ).reshape(-1, 3)
    ctx.progress(15, "构建地层界面")

    layers = sorted(spec.stratigraphy, key=lambda s: s.order)
    n_layers = max(len(layers), 1)
    if not layers:
        layers = [_fallback_layer()]

    shifted_z = centers[:, 2].copy()
    fault_mask = np.zeros(centers.shape[0], dtype=bool)
    for fault in spec.faults:
        if fault.status != "active":
            continue
        if fault.params.throw_v <= 0 and fault.params.throw_h <= 0:
            continue
        mask = _fault_side_mask(centers, fault)
        if fault.params.throw_v > 0:
            shifted_z[mask] -= fault.params.throw_v
        fault_mask |= mask

    ctx.progress(40, "插值地层界面")
    columns = np.stack(np.meshgrid(x_mid, y_mid, indexing="ij"), axis=-1).reshape(-1, 2)
    # 水平落差通过把受影响一侧的界面查询点沿断层法向平移实现；
    # 位移量作用于整个地层柱，从而在分层结果中体现横向错动。
    query_xy = _fault_adjusted_xy(columns, spec.faults)
    boundaries_all = _layer_boundaries(spec, layers, query_xy)
    col_index = np.repeat(np.arange(columns.shape[0]), nz)
    boundaries = boundaries_all[1:n_layers][:, col_index]
    layer_index = _assign_layers(shifted_z, boundaries, n_layers)
    ctx.progress(75, "装配统一模型")

    # 层序索引 0..n-1 对应 sorted(layers) 的位置（不依赖 order 是否连续）
    code_to_order = {layer.layer_code: index for index, layer in enumerate(layers)}
    order_to_code = {index: layer.layer_code for index, layer in enumerate(layers)}
    codes = _map_codes(layer_index, order_to_code)

    layer_surfaces = _surface_meta(boundaries_all, x_mid, y_mid, nx, ny, order_to_code)

    cell_attr = {
        "layer": layer_index.astype(np.int32),
        "fault_affected": fault_mask.astype(np.int8),
    }
    face_groups = {
        code: np.nonzero(codes == code)[0].tolist() for code in order_to_code.values()
    }
    mesh = UMesh(
        vertices=vertices,
        cells=cells,
        cell_types=np.full(len(cells), VTK_HEXAHEDRON, dtype=np.int32),
        cell_attr=cell_attr,
        face_groups=face_groups,
        meta={
            "engine": engine_name,
            "resolution": [nx, ny, nz],
            "layer_codes": list(order_to_code.values()),
            "crs": spec.bounds.model_dump(),
            "duration_s": round(time.perf_counter() - started, 3),
            "n_drillholes": len(spec.drillholes),
            "n_faults": len(spec.faults),
            "code_to_order": code_to_order,
            "layer_surfaces": layer_surfaces,
        },
    )
    ctx.progress(100, "完成")
    return mesh


def _interpolate_surface(
    spec: ModelingSpec, pts: np.ndarray, vals: np.ndarray, xy: np.ndarray, fallback: float
) -> np.ndarray:
    """在 ``xy`` 上插值一张界面高程；样本不足时回退到 ``fallback`` 平面。"""
    method = ensure_interp_method(spec.interp.method)
    if len(pts) < 2:
        return np.full(xy.shape[0], fallback, dtype=np.float64)
    if method == "cokriging" and spec.seismic and len(pts) >= 4:
        est = _cokrige_interface(spec, pts, vals, xy)
    elif method in ("kriging", "cokriging") and len(pts) >= 4:
        est = kriging(
            pts,
            vals,
            xy,
            variogram=spec.interp.variogram_model,
            nugget=spec.interp.nugget,
            sill=spec.interp.sill,
            range_=spec.interp.range_,
            k=recommended_k(len(pts), spec.interp.neighbor),
        )
    elif method == "dsi":
        est = dsi(
            pts,
            vals,
            xy,
            k=recommended_k(len(pts), spec.interp.neighbor),
            iterations=20,
        )
    else:
        est = idw(pts, vals, xy, k=recommended_k(len(pts), spec.interp.neighbor))
    return np.where(np.isfinite(est), est, fallback)


def _layer_boundaries(spec: ModelingSpec, layers: List, columns: np.ndarray) -> np.ndarray:
    """返回每层顶/底界面高程 ``(n_layers+1, n_cols)``（自上而下递减）。

    第 0 行是顶层顶面（地表），最后一行是底层底面；中间行与内部界面一致。
    查询点先按 XY 去重后插值再映射回原顺序，避免规则网格 ``nz`` 倍冗余。
    """
    n_layers = len(layers)
    zmin, zmax = spec.bounds.min[2], spec.bounds.max[2]
    columns = np.asarray(columns, dtype=np.float64)
    unique_xy, inverse = np.unique(columns, axis=0, return_inverse=True)
    inverse = np.asarray(inverse).reshape(-1)

    surfaces: List[np.ndarray] = []
    saw_samples = False
    inside_bounds = False
    for index in range(n_layers + 1):
        if index == 0:
            pts, vals = _interface_samples(spec.drillholes, layers[0].layer_code)
            fallback = zmax
        elif index == n_layers:
            pts, vals = _bottom_samples(spec.drillholes, layers[-1].layer_code)
            fallback = zmin
        else:
            pts, vals = _interface_samples(spec.drillholes, layers[index].layer_code)
            fallback = zmax + (zmin - zmax) * (index / n_layers)
        if len(vals):
            saw_samples = True
            if np.any((vals >= zmin) & (vals <= zmax)):
                inside_bounds = True
        surfaces.append(_interpolate_surface(spec, pts, vals, unique_xy, fallback))
    if saw_samples and not inside_bounds:
        raise GeoModelError(
            ErrorCode.MDL_BND_001,
            "地层界面高程全部位于模型 Z 范围之外，无法生成有效分层模型。",
            context={"zmin": float(zmin), "zmax": float(zmax), "n_drillholes": len(spec.drillholes)},
        )
    stacked = np.vstack(surfaces)
    # 保证自上而下单调递减，避免各界面独立插值相互穿插（层厚为负）
    if stacked.shape[0] > 1:
        stacked = np.minimum.accumulate(stacked, axis=0)
    return stacked[:, inverse]


def _interface_surfaces(spec: ModelingSpec, layers: List, xy: np.ndarray) -> np.ndarray:
    """返回内部界面高程 ``(k-1, n_xy)``（k=层数），自上而下递减。"""
    return _layer_boundaries(spec, layers, xy)[1 : len(layers)]


def _surface_meta(boundaries_all, x_mid, y_mid, nx, ny, order_to_code):
    """把每层顶/底界面整理为供视口渲染层实体的规则网格（失败则返回 None）。"""
    try:
        grids = boundaries_all.reshape(len(order_to_code) + 1, nx, ny)
    except ValueError:  # 非规则网格时不提供
        return None
    return {
        "x": [float(v) for v in x_mid],
        "y": [float(v) for v in y_mid],
        "z": grids.tolist(),
        "codes": list(order_to_code.values()),
    }


def _cokrige_interface(spec: ModelingSpec, pts: np.ndarray, vals: np.ndarray, xy: np.ndarray) -> np.ndarray:
    """以地震趋势为协变量做协同克里金（可选高斯平滑）。"""
    from geomodel3d.algorithms.interpolation import cokriging
    from geomodel3d.algorithms.spatial import smooth_values

    layer = spec.seismic[0]
    secondary = np.asarray(layer.points, dtype=np.float64)
    if secondary.shape[0] < 2:
        return kriging(
            pts, vals, xy,
            variogram=spec.interp.variogram_model, nugget=spec.interp.nugget,
            sill=spec.interp.sill, range_=spec.interp.range_,
            k=recommended_k(len(pts), spec.interp.neighbor),
        )
    secondary_values = secondary[:, 2]
    if layer.smooth > 0:
        secondary_values = smooth_values(secondary, secondary_values, layer.smooth, anisotropy=0.5)
    return cokriging(
        pts, vals, xy, secondary[:, :2], secondary_values,
        trend_weight=spec.interp.trend_weight,
        variogram=spec.interp.variogram_model, nugget=spec.interp.nugget,
        sill=spec.interp.sill, range_=spec.interp.range_,
        k=recommended_k(len(pts), spec.interp.neighbor),
    )


def _axes(bounds, nx: int, ny: int, nz: int):
    xs = np.linspace(bounds.min[0], bounds.max[0], nx + 1)
    ys = np.linspace(bounds.min[1], bounds.max[1], ny + 1)
    zs = np.linspace(bounds.min[2], bounds.max[2], nz + 1)
    return xs, ys, zs


def _mid(axis: np.ndarray) -> np.ndarray:
    return 0.5 * (axis[:-1] + axis[1:])


def _map_codes(layer_index: np.ndarray, order_to_code: Dict[int, str]) -> np.ndarray:
    """向量化地把层序索引映射为地层代码（避免逐单元 Python 循环）。"""
    unique, inverse = np.unique(layer_index, return_inverse=True)
    lookup = np.array(
        [order_to_code.get(int(value), "UNK") for value in unique], dtype=object
    )
    return lookup[inverse.reshape(-1)]


def _voxel_grid(xs: np.ndarray, ys: np.ndarray, zs: np.ndarray):
    """生成规则六面体网格的顶点与连接表（VTK_HEXAHEDRON 顺序）。"""
    nx, ny, nz = len(xs) - 1, len(ys) - 1, len(zs) - 1
    gx, gy, gz = np.meshgrid(xs, ys, zs, indexing="ij")
    vertices = np.column_stack([gx.ravel(), gy.ravel(), gz.ravel()])
    stride_y = nz + 1
    stride_x = (ny + 1) * (nz + 1)

    def nid(i, j, k):
        return i * stride_x + j * stride_y + k

    ii, jj, kk = np.meshgrid(
        np.arange(nx), np.arange(ny), np.arange(nz), indexing="ij"
    )
    ii, jj, kk = ii.ravel(), jj.ravel(), kk.ravel()
    corners = np.column_stack(
        [
            nid(ii, jj, kk),
            nid(ii + 1, jj, kk),
            nid(ii + 1, jj + 1, kk),
            nid(ii, jj + 1, kk),
            nid(ii, jj, kk + 1),
            nid(ii + 1, jj, kk + 1),
            nid(ii + 1, jj + 1, kk + 1),
            nid(ii, jj + 1, kk + 1),
        ]
    )
    return vertices, corners


def _fault_frame(fault: Fault, fallback_center: np.ndarray):
    """返回断层的平面中心、走向单位向量与法向单位向量。

    优先用迹线方向推法向，保证与影响域/迹线一致；无迹线时回退到
    ``fallback_center`` 与走向约定（自正北顺时针）。
    """
    trace = np.asarray(fault.trace, dtype=np.float64)
    if trace.shape[0] > 0:
        center = trace[:, :2].mean(axis=0)
    else:
        center = np.asarray(fallback_center, dtype=np.float64)

    strike = np.deg2rad(fault.params.strike)
    fallback_along = np.array([np.sin(strike), np.cos(strike)])
    if trace.shape[0] >= 2:
        delta = trace[-1, :2] - trace[0, :2]
        norm = float(np.linalg.norm(delta))
        along = delta / norm if norm > 1e-9 else fallback_along
    else:
        along = fallback_along
    normal = np.array([-along[1], along[0]])
    return center, along, normal


def _fault_side_mask(centers: np.ndarray, fault: Fault) -> np.ndarray:
    """基于断层走向的上下盘判定（垂直于断层面分盘）。"""
    center, _, normal = _fault_frame(fault, centers[:, :2].mean(axis=0))
    signed = (
        (centers[:, 0] - center[0]) * normal[0]
        + (centers[:, 1] - center[1]) * normal[1]
    )
    return signed > 0


def _fault_adjusted_xy(
    points: np.ndarray,
    faults: List[Fault],
    *,
    edited_id: str = "",
    old_throw_h: float = 0.0,
    blend: Optional[np.ndarray] = None,
) -> np.ndarray:
    """按断层水平落差返回调整后的平面查询坐标。

    正侧（与 ``_fault_side_mask`` 同侧）沿断层法向平移 ``throw_h``。局部重建模
    时通过 ``blend`` 在旧/新水平落差之间做 smoothstep 过渡，避免接缝跳变。
    所有断层掩码均基于原始坐标计算，避免多断层之间顺序依赖。
    """
    base = np.asarray(points, dtype=np.float64)
    if base.ndim != 2 or base.shape[1] < 2:
        raise ValueError("points 必须是 (N, 2) 或 (N, 3) 数组")
    if base.shape[0] == 0:
        return base[:, :2].copy()

    base_xy = base[:, :2]
    adjusted = base_xy.copy()
    fallback_center = base_xy.mean(axis=0)
    blend_array = None if blend is None else np.asarray(blend, dtype=np.float64)

    for fault in faults:
        if fault.status != "active":
            continue
        new_throw = float(fault.params.throw_h)
        is_edited = blend_array is not None and edited_id == fault.fault_id
        if new_throw <= 0 and not (is_edited and old_throw_h > 0):
            continue

        center, along, normal = _fault_frame(fault, fallback_center)
        signed = (
            (base_xy[:, 0] - center[0]) * normal[0]
            + (base_xy[:, 1] - center[1]) * normal[1]
        )
        mask = signed > 0
        if not mask.any():
            continue

        if is_edited:
            offset = blend_array * new_throw + (1.0 - blend_array) * float(old_throw_h)
        else:
            offset = np.full(base_xy.shape[0], new_throw)

        # 平移方向按断层性质区分：
        # - 平移断层：沿走向；
        # - 逆断层：沿法向的反方向（与正断层相反）；
        # - 正断层/未知：沿法向。
        if fault.side == FaultSide.STRIKE_SLIP:
            direction = along
            sign = 1.0
        elif fault.side == FaultSide.REVERSE:
            direction = normal
            sign = -1.0
        else:
            direction = normal
            sign = 1.0
        adjusted[mask] += direction[None, :] * (sign * offset[mask, None])

    return adjusted


def _assign_layers(z: np.ndarray, boundaries: np.ndarray, n_layers: int) -> np.ndarray:
    """按界面高程把单元分配到地层序号（0 为最浅层）。"""
    if boundaries.shape[0] == 0:
        return np.zeros(z.shape[0], dtype=np.int32)
    counts = np.zeros(z.shape[0], dtype=np.int32)
    for surface in boundaries:
        counts += (z <= surface).astype(np.int32)
    return np.clip(counts, 0, n_layers - 1)


def _interface_samples(
    holes: List[Drillhole], layer_code: str
) -> Tuple[np.ndarray, np.ndarray]:
    """收集某地层顶面界面点（大地坐标 z = 孔口高程 - 层顶深）。"""
    pts: List[List[float]] = []
    vals: List[float] = []
    for hole in holes:
        for interval in hole.intervals:
            if interval.layer_code == layer_code:
                pts.append([hole.x, hole.y])
                vals.append(hole.collar_elev - interval.depth_top)
                break
    if not pts:
        return np.empty((0, 2)), np.empty((0,))
    return np.asarray(pts, dtype=np.float64), np.asarray(vals, dtype=np.float64)


def _bottom_samples(
    holes: List[Drillhole], layer_code: str
) -> Tuple[np.ndarray, np.ndarray]:
    """收集某地层底面界面点（大地坐标 z = 孔口高程 - 层底深）。"""
    pts: List[List[float]] = []
    vals: List[float] = []
    for hole in holes:
        bottoms = [
            interval.depth_bottom
            for interval in hole.intervals
            if interval.layer_code == layer_code
        ]
        if bottoms:
            pts.append([hole.x, hole.y])
            vals.append(hole.collar_elev - max(bottoms))
    if not pts:
        return np.empty((0, 2)), np.empty((0,))
    return np.asarray(pts, dtype=np.float64), np.asarray(vals, dtype=np.float64)


def _fallback_layer():
    from geomodel3d.config.schema import StratigraphyEntry

    return StratigraphyEntry(layer_code="L0", name="默认地层", order=0)


__all__ = ["build_layered_mesh"]
