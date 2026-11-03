"""层实体几何（顶/底规则网格 -> 封闭三角面片实体）。

对齐参考项目做法：每层由顶面、底面与四侧壁组成封闭实体，供视口以光滑
着色渲染出"千层饼"式地质体；不依赖 pyvista，返回顶点与 VTK 风格面索引。
"""
from __future__ import annotations

from typing import Tuple

import numpy as np


def layer_solid_geometry(
    xs: np.ndarray,
    ys: np.ndarray,
    top: np.ndarray,
    bottom: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """由顶/底规则网格构建封闭层实体。

    Args:
        xs: ``(nx,)`` X 节点坐标。
        ys: ``(ny,)`` Y 节点坐标。
        top: ``(nx, ny)`` 顶面高程（``top[ix, iy]``）。
        bottom: ``(nx, ny)`` 底面高程。

    Returns:
        ``(vertices (N,3), faces)``，``faces`` 为 VTK 风格扁平数组
        ``[3, i, j, k, 3, ...]``。网格退化时返回空面片。
    """
    xs = np.asarray(xs, dtype=np.float64).reshape(-1)
    ys = np.asarray(ys, dtype=np.float64).reshape(-1)
    nx, ny = xs.shape[0], ys.shape[0]
    if nx < 2 or ny < 2:
        return np.empty((0, 3)), np.empty((0,), dtype=np.int64)

    top = np.asarray(top, dtype=np.float64).reshape(nx, ny)
    bottom = np.asarray(bottom, dtype=np.float64).reshape(nx, ny)
    valid = np.isfinite(top) & np.isfinite(bottom)

    xx, yy = np.meshgrid(xs, ys, indexing="ij")
    top_pts = np.column_stack([xx.ravel(), yy.ravel(), top.ravel()])
    bot_pts = np.column_stack([xx.ravel(), yy.ravel(), bottom.ravel()])
    vertices = np.vstack([top_pts, bot_pts])
    offset = nx * ny

    def idx(ix: int, iy: int) -> int:
        return ix * ny + iy

    def ok(*nodes) -> bool:
        for ix, iy in nodes:
            if not valid[ix, iy]:
                return False
        return True

    faces = []

    def quad(a: int, b: int, c: int, d: int) -> None:
        faces.append(3)
        faces.extend((a, b, c))
        faces.append(3)
        faces.extend((a, c, d))

    for ix in range(nx - 1):
        for iy in range(ny - 1):
            if not ok((ix, iy), (ix + 1, iy), (ix + 1, iy + 1), (ix, iy + 1)):
                continue
            a, b = idx(ix, iy), idx(ix + 1, iy)
            c, d = idx(ix + 1, iy + 1), idx(ix, iy + 1)
            quad(a, b, c, d)  # 顶面（+Z 朝外）
            # 底面反向绕序
            faces.append(3)
            faces.extend((offset + a, offset + c, offset + b))
            faces.append(3)
            faces.extend((offset + a, offset + d, offset + c))

    for ix in range(nx - 1):  # 南墙 iy=0（外法向 -Y）
        if not ok((ix, 0), (ix + 1, 0)):
            continue
        a, b = idx(ix, 0), idx(ix + 1, 0)
        quad(a, offset + a, offset + b, b)

    base = ny - 1
    for ix in range(nx - 1):  # 北墙 iy=ny-1（外法向 +Y）
        if not ok((ix, base), (ix + 1, base)):
            continue
        a, b = idx(ix, base), idx(ix + 1, base)
        quad(a, b, offset + b, offset + a)

    for iy in range(ny - 1):  # 西墙 ix=0（外法向 -X）
        if not ok((0, iy), (0, iy + 1)):
            continue
        a, b = idx(0, iy), idx(0, iy + 1)
        quad(a, offset + a, offset + b, b)

    last = nx - 1
    for iy in range(ny - 1):  # 东墙 ix=nx-1（外法向 +X）
        if not ok((last, iy), (last, iy + 1)):
            continue
        a, b = idx(last, iy), idx(last, iy + 1)
        quad(a, offset + a, offset + b, b)

    if not faces:
        return vertices, np.empty((0,), dtype=np.int64)
    return vertices, np.asarray(faces, dtype=np.int64)


__all__ = ["layer_solid_geometry"]
