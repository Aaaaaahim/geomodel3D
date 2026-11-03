"""插值算法（6.1）。

headless 可测，不依赖 Qt / 引擎。提供 IDW、普通克里金、协同克里金，
以及开源近似 DSI（离散光滑插值：邻域拉普拉斯平滑 + 硬约束）。
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from geomodel3d.algorithms.spatial import knn_indices
from geomodel3d.config.errors import ErrorCode, GeoModelError
from geomodel3d.config.schema import IMPLEMENTED_INTERP_METHODS


def recommended_k(n_samples: int, neighbor: int, *, cap: int = 256) -> int:
    """推荐邻域数：样本较少时用全量样本，可一次性批量求解，远快于逐点邻域。

    当 ``n_samples <= cap`` 时返回 ``n_samples``，触发 :func:`kriging` 的
    单次线性求解分支（矩阵一次分解、右端项按所有查询点拼接）；否则回退到
    用户配置的局部邻域数。
    """
    n_samples = int(n_samples)
    neighbor = int(neighbor)
    if n_samples <= cap:
        return n_samples
    return min(neighbor, n_samples)


def ensure_interp_method(method: str) -> str:
    """校验插值方法已实现；未实现时显式报错，杜绝静默降级为 IDW。

    ``gempy`` 仅为历史工程兼容值保留在 schema 白名单中，真正执行插值前
    必须在此拦截；``dsi`` 由开源近似实现提供。
    """
    if method not in IMPLEMENTED_INTERP_METHODS:
        raise GeoModelError(
            ErrorCode.MDL_IPL_002,
            f"插值方法 {method!r} 尚未实现，可选 {list(IMPLEMENTED_INTERP_METHODS)}。",
            context={"method": method, "supported": list(IMPLEMENTED_INTERP_METHODS)},
            suggestion=(
                "请在参数面板选择 idw / kriging / cokriging / dsi；"
                "GemPy 插值需对应的扩展模块支持。"
            ),
        )
    return method


def idw(
    points: np.ndarray,
    values: np.ndarray,
    query: np.ndarray,
    *,
    power: float = 2.0,
    k: Optional[int] = None,
    epsilon: float = 1e-12,
) -> np.ndarray:
    """反距离加权插值。

    Args:
        points: ``(N, 3)`` 样本坐标。
        values: ``(N,)`` 样本值。
        query: ``(M, 3)`` 查询坐标。
        power: 距离幂次。
        k: 邻域截断数量；``None`` 表示使用全部样本。
    """
    points = np.asarray(points, dtype=np.float64)
    values = np.asarray(values, dtype=np.float64).reshape(-1)
    query = np.asarray(query, dtype=np.float64)
    if points.shape[0] != values.shape[0]:
        raise ValueError("points 与 values 数量不一致")
    if points.shape[0] == 0:
        return np.full(query.shape[0], np.nan)

    if k is not None and 0 < k < points.shape[0]:
        idx = knn_indices(points, query, k)
        dist = np.linalg.norm(query[:, None, :] - points[idx], axis=2)
        vals = values[idx]
    else:
        dist = np.linalg.norm(query[:, None, :] - points[None, :, :], axis=2)
        vals = np.broadcast_to(values[None, :], dist.shape)

    exact = dist < epsilon
    with np.errstate(divide="ignore", invalid="ignore"):
        weights = 1.0 / np.power(dist + epsilon, power)
    weights[exact] = 0.0
    result = np.sum(weights * vals, axis=1) / np.sum(weights, axis=1)
    # 命中样本点时严格等于观测值（硬约束）
    exact_rows = exact.any(axis=1)
    if exact_rows.any():
        first = exact.argmax(axis=1)
        result[exact_rows] = vals[np.arange(dist.shape[0]), first][exact_rows]
    return result


def kriging(
    points: np.ndarray,
    values: np.ndarray,
    query: np.ndarray,
    *,
    variogram: str = "spherical",
    nugget: float = 0.0,
    sill: float = 1.0,
    range_: float = 1000.0,
    k: int = 16,
) -> np.ndarray:
    """普通克里金（局部邻域，球形/指数/高斯变差函数）。"""
    points = np.asarray(points, dtype=np.float64)
    values = np.asarray(values, dtype=np.float64).reshape(-1)
    query = np.asarray(query, dtype=np.float64)
    if points.shape[0] < 4:
        return idw(points, values, query, k=k)
    if points.shape[0] <= k:
        neighbors = np.arange(points.shape[0])
        return _kriging_solve(points, values, query, neighbors, variogram, nugget, sill, range_)

    idx = knn_indices(points, query, k)
    out = np.empty(query.shape[0])
    for row, neighbor_ids in enumerate(idx):
        out[row] = _kriging_solve(
            points, values, query[row : row + 1], neighbor_ids, variogram, nugget, sill, range_
        )[0]
    return out


def _kriging_solve(
    points: np.ndarray,
    values: np.ndarray,
    query: np.ndarray,
    neighbor_ids: np.ndarray,
    variogram: str,
    nugget: float,
    sill: float,
    range_: float,
) -> np.ndarray:
    pts = points[neighbor_ids]
    vals = values[neighbor_ids]
    n = len(pts)
    d = _pairwise(pts)
    gamma = _gamma(d, variogram, nugget, sill, range_)
    matrix = np.zeros((n + 1, n + 1))
    matrix[:n, :n] = gamma
    matrix[n, :n] = 1.0
    matrix[:n, n] = 1.0
    rhs = np.zeros((n + 1, query.shape[0]))
    rhs[:n] = _gamma(
        np.linalg.norm(pts[:, None, :] - query[None, :, :], axis=2),
        variogram,
        nugget,
        sill,
        range_,
    )
    rhs[n] = 1.0
    try:
        weights = np.linalg.solve(matrix, rhs)[:n]
    except np.linalg.LinAlgError:
        return idw(pts, vals, query, k=n)
    return (weights * vals[:, None]).sum(axis=0)


def dsi(
    points: np.ndarray,
    values: np.ndarray,
    query: np.ndarray,
    *,
    k: int = 8,
    iterations: int = 20,
    relaxation: float = 0.5,
    **_: object,
) -> np.ndarray:
    """开源近似 DSI：IDW 初值 + 查询点邻域拉普拉斯平滑 + 样本硬约束。

    这不是 OpenGeode 商业 DSI 的逐位实现，而是离散光滑插值的轻量近似：
    在保持样本点精确值的同时，使查询点邻域内的估计值连续、光滑。
    """
    points = np.asarray(points, dtype=np.float64)
    values = np.asarray(values, dtype=np.float64).reshape(-1)
    query = np.asarray(query, dtype=np.float64)
    if points.shape[0] == 0:
        return np.full(query.shape[0], np.nan)
    if query.shape[0] == 0:
        return np.zeros(0)

    estimate = idw(points, values, query, k=max(1, min(int(k), points.shape[0])))
    if query.shape[0] < 2 or iterations <= 0:
        return estimate

    neighbor_count = max(2, min(int(k), query.shape[0]))
    neighbors = knn_indices(query, query, neighbor_count)
    nearest = knn_indices(points, query, 1)[:, 0]
    exact = np.linalg.norm(query - points[nearest], axis=1) < 1e-9

    for _ in range(int(iterations)):
        neighbor_mean = estimate[neighbors].mean(axis=1)
        estimate = estimate + float(relaxation) * (neighbor_mean - estimate)
        if exact.any():
            estimate[exact] = values[nearest[exact]]
    return np.nan_to_num(estimate, nan=0.0)


def kriging_with_drift(
    points: np.ndarray,
    values: np.ndarray,
    query: np.ndarray,
    drift_points: np.ndarray,
    drift_query: np.ndarray,
    *,
    variogram: str = "spherical",
    nugget: float = 0.0,
    sill: float = 1.0,
    range_: float = 1000.0,
    k: int = 16,
    trend_weight: float = 1.0,
) -> np.ndarray:
    """带外部漂移的克里金（KED）。

    ``drift_points``/``drift_query`` 为协变量（如地震趋势）在样本点/查询点的值。
    ``trend_weight=0`` 退化为普通克里金，``=1`` 使用完整趋势。
    """
    points = np.asarray(points, dtype=np.float64)
    values = np.asarray(values, dtype=np.float64).reshape(-1)
    query = np.asarray(query, dtype=np.float64)
    drift_points = np.asarray(drift_points, dtype=np.float64).reshape(-1)
    drift_query = np.asarray(drift_query, dtype=np.float64).reshape(-1)
    if points.shape[0] < 4:
        return idw(points, values, query, k=k)

    mean_drift = float(drift_points.mean()) if drift_points.size else 0.0
    s = trend_weight * drift_points + (1.0 - trend_weight) * mean_drift
    s0 = trend_weight * drift_query + (1.0 - trend_weight) * mean_drift
    if float(np.ptp(s)) < 1e-12:
        return kriging(points, values, query, variogram=variogram, nugget=nugget,
                       sill=sill, range_=range_, k=k)

    if points.shape[0] <= k:
        neighbors = np.arange(points.shape[0])
        return _ked_solve(points, values, s, query, s0, neighbors, variogram, nugget, sill, range_)

    idx = knn_indices(points, query, k)
    out = np.empty(query.shape[0])
    for row, neighbor_ids in enumerate(idx):
        out[row] = _ked_solve(
            points, values, s, query[row : row + 1], s0[row : row + 1],
            neighbor_ids, variogram, nugget, sill, range_,
        )[0]
    return out


def cokriging(
    points: np.ndarray,
    values: np.ndarray,
    query: np.ndarray,
    secondary_points: np.ndarray,
    secondary_values: np.ndarray,
    *,
    trend_weight: float = 0.6,
    variogram: str = "spherical",
    nugget: float = 0.0,
    sill: float = 1.0,
    range_: float = 1000.0,
    k: int = 16,
) -> np.ndarray:
    """协同克里金（钻孔主变量 + 地震协变量）。

    以协变量（地震趋势）构造外部漂移，与主变量（钻孔界面/品位）融合。
    """
    secondary_points = np.asarray(secondary_points, dtype=np.float64)
    secondary_values = np.asarray(secondary_values, dtype=np.float64).reshape(-1)
    if secondary_points.shape[0] < 2:
        return kriging(points, values, query, variogram=variogram, nugget=nugget,
                       sill=sill, range_=range_, k=k)
    drift_points = _secondary_at(secondary_points, secondary_values, points)
    drift_query = _secondary_at(secondary_points, secondary_values, query)
    return kriging_with_drift(
        points, values, query, drift_points, drift_query,
        variogram=variogram, nugget=nugget, sill=sill, range_=range_, k=k,
        trend_weight=trend_weight,
    )


def _secondary_at(secondary_points: np.ndarray, secondary_values: np.ndarray, targets: np.ndarray) -> np.ndarray:
    """用反距离加权把协变量采样到目标点（按 XY 平面）。"""
    return idw(secondary_points[:, :2], secondary_values, targets[:, :2], k=8)


def _ked_solve(
    points: np.ndarray,
    values: np.ndarray,
    drift: np.ndarray,
    query: np.ndarray,
    drift_query: np.ndarray,
    neighbor_ids: np.ndarray,
    variogram: str,
    nugget: float,
    sill: float,
    range_: float,
) -> np.ndarray:
    pts = points[neighbor_ids]
    vals = values[neighbor_ids]
    s = drift[neighbor_ids]
    n = len(pts)
    gamma = _gamma(_pairwise(pts), variogram, nugget, sill, range_)
    matrix = np.zeros((n + 2, n + 2))
    matrix[:n, :n] = gamma
    matrix[n, :n] = 1.0
    matrix[:n, n] = 1.0
    matrix[n + 1, :n] = s
    matrix[:n, n + 1] = s
    rhs = np.zeros((n + 2, query.shape[0]))
    rhs[:n] = _gamma(
        np.linalg.norm(pts[:, None, :] - query[None, :, :], axis=2),
        variogram, nugget, sill, range_,
    )
    rhs[n] = 1.0
    rhs[n + 1] = drift_query
    try:
        weights = np.linalg.solve(matrix, rhs)[:n]
    except np.linalg.LinAlgError:
        return idw(pts, vals, query, k=n)
    return (weights * vals[:, None]).sum(axis=0)


def _gamma(distance: np.ndarray, model: str, nugget: float, sill: float, range_: float) -> np.ndarray:
    ratio = np.clip(distance / max(range_, 1e-9), 0.0, 1.0)
    if model == "exponential":
        base = 1.0 - np.exp(-3.0 * ratio)
    elif model == "gaussian":
        base = 1.0 - np.exp(-3.0 * ratio**2)
    else:  # spherical
        base = 1.5 * ratio - 0.5 * ratio**3
    return nugget + (sill - nugget) * base


def _pairwise(pts: np.ndarray) -> np.ndarray:
    diff = pts[:, None, :] - pts[None, :, :]
    return np.sqrt((diff**2).sum(axis=2))


__all__ = [
    "idw",
    "kriging",
    "dsi",
    "kriging_with_drift",
    "cokriging",
    "ensure_interp_method",
    "recommended_k",
]
