"""空间索引工具（可选 scipy，纯 NumPy 兜底）。

用于插值/储量的最近邻查询与网格修复的重合顶点检测。若安装了 scipy，
优先使用 ``cKDTree``；否则使用分块暴力最近邻与均匀网格哈希，
从而在冻结版中可剔除 scipy（约 90MB）而保持功能。
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np

_CHUNK = 20000
# 单次 (chunk, n) 距离矩阵的元素上限；距离计算会有约 3~4 个同尺寸临时数组，
# 取 5e5 使峰值内存约几十 MB。
_DISTANCE_BUDGET = 500_000


def _query_chunk_size(n_points: int) -> int:
    """按参考点数量自适应查询分块，避免 (chunk, n, 3) 距离矩阵爆内存。"""
    if n_points <= 0:
        return _CHUNK
    return max(1, min(_CHUNK, _DISTANCE_BUDGET // n_points))


def knn_indices(points: np.ndarray, queries: np.ndarray, k: int) -> np.ndarray:
    """返回每个查询点的 ``k`` 个最近邻索引 ``(M, k)``。"""
    points = np.asarray(points, dtype=np.float64)
    queries = np.asarray(queries, dtype=np.float64)
    n = points.shape[0]
    m = queries.shape[0]
    if n == 0 or m == 0:
        return np.empty((m, 0), dtype=np.int64)
    k = max(1, min(k, n))
    tree = _load_kdtree()
    if tree is not None:
        _, idx = tree(points).query(queries, k=k)
        idx = np.atleast_2d(idx)
        if idx.shape[0] != m:
            idx = idx.T
        return idx.astype(np.int64)

    out = np.empty((m, k), dtype=np.int64)
    chunk_size = _query_chunk_size(n)
    for start in range(0, m, chunk_size):
        end = min(start + chunk_size, m)
        chunk = queries[start:end]
        dist = ((chunk[:, None, :] - points[None, :, :]) ** 2).sum(axis=2)
        if k < n:
            part = np.argpartition(dist, k - 1, axis=1)[:, :k]
        else:
            part = np.broadcast_to(np.arange(n), (chunk.shape[0], n))
        rows = np.arange(chunk.shape[0])[:, None]
        order = np.argsort(dist[rows, part], axis=1)
        out[start:end] = part[rows, order]
    return out


def nearest_distances(points: np.ndarray, queries: np.ndarray) -> np.ndarray:
    """返回每个查询点到最近样本点的欧氏距离。"""
    points = np.asarray(points, dtype=np.float64)
    queries = np.asarray(queries, dtype=np.float64)
    if points.shape[0] == 0:
        return np.full(queries.shape[0], np.inf)
    if queries.shape[0] == 0:
        return np.zeros(0)
    tree = _load_kdtree()
    if tree is not None:
        distance, _ = tree(points).query(queries)
        return distance
    out = np.empty(queries.shape[0])
    chunk_size = _query_chunk_size(points.shape[0])
    for start in range(0, queries.shape[0], chunk_size):
        end = min(start + chunk_size, queries.shape[0])
        chunk = queries[start:end]
        dist = np.sqrt(((chunk[:, None, :] - points[None, :, :]) ** 2).sum(axis=2))
        out[start:end] = dist.min(axis=1)
    return out


def pairs_within_tolerance(points: np.ndarray, tolerance: float) -> List[Tuple[int, int]]:
    """返回距离不超过 ``tolerance`` 的点对 ``(i, j), i < j``（均匀网格哈希）。"""
    points = np.asarray(points, dtype=np.float64)
    n = points.shape[0]
    if n < 2 or tolerance <= 0:
        return []
    cells = np.floor(points / tolerance).astype(np.int64)
    buckets: dict = {}
    for index in range(n):
        buckets.setdefault(tuple(cells[index]), []).append(index)
    pairs: List[Tuple[int, int]] = []
    seen: set = set()
    tol2 = tolerance * tolerance
    offsets = [
        (dx, dy, dz)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        for dz in (-1, 0, 1)
    ]
    for key, indices in buckets.items():
        for offset in offsets:
            neighbor = (key[0] + offset[0], key[1] + offset[1], key[2] + offset[2])
            others = buckets.get(neighbor)
            if not others:
                continue
            for i in indices:
                for j in others:
                    if i >= j:
                        continue
                    pair = (i, j)
                    if pair in seen:
                        continue
                    delta = points[i] - points[j]
                    if float(delta @ delta) <= tol2:
                        seen.add(pair)
                        pairs.append(pair)
    return pairs


def smooth_values(
    points: np.ndarray,
    values: np.ndarray,
    smooth: float,
    k: int = 8,
    anisotropy: float = 1.0,
) -> np.ndarray:
    """对点属性做高斯邻域平滑。

    Args:
        smooth: 邻域间距倍数，``0`` 表示不平滑。
        anisotropy: Z 向距离权重（``<1`` 表示水平方向主导的各向异性平滑）。
    """
    points = np.asarray(points, dtype=np.float64)
    values = np.asarray(values, dtype=np.float64).reshape(-1)
    n = points.shape[0]
    if smooth <= 0 or n < 2:
        return values.copy()
    k = max(2, min(k, n))
    idx = knn_indices(points, points, k)
    delta = points[:, None, :] - points[idx]
    if anisotropy != 1.0:
        delta = delta.copy()
        # anisotropy<1 表示削弱垂向影响（Z 距离按 1/anisotropy 放大）
        delta[:, :, 2] /= max(anisotropy, 1e-6)
    distances = np.sqrt((delta**2).sum(axis=2))
    positive = distances[distances > 0]
    spacing = float(np.median(positive)) if positive.size else 1.0
    if spacing <= 0:
        spacing = 1.0
    sigma = max(smooth * spacing, 1e-9)
    weights = np.exp(-(distances**2) / (2.0 * sigma**2))
    return (weights * values[idx]).sum(axis=1) / weights.sum(axis=1)


def _load_kdtree():
    try:
        from scipy.spatial import cKDTree

        return cKDTree
    except Exception:  # noqa: BLE001 - 未安装 scipy 时使用 NumPy 兜底
        return None


__all__ = ["knn_indices", "nearest_distances", "pairs_within_tolerance", "smooth_values"]
