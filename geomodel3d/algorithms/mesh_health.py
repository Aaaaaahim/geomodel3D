"""网格体检（M6-F6 / BR-09）。

检测空网格、非有限坐标、退化单元、重复单元、非流形边、自由边与
法向/体积一致性，输出分级报告，供导出前检查单与修复流程使用。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Tuple

import numpy as np

from geomodel3d.engine.umesh import UMesh


class HealthSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class HealthIssue:
    severity: HealthSeverity
    code: str
    message: str
    count: int = 0


@dataclass
class HealthReport:
    issues: List[HealthIssue] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not any(issue.severity == HealthSeverity.ERROR for issue in self.issues)

    def add(self, severity: HealthSeverity, code: str, message: str, count: int = 0) -> None:
        self.issues.append(HealthIssue(severity, code, message, count))

    def summary(self) -> Dict[str, int]:
        return {
            "error": sum(1 for i in self.issues if i.severity == HealthSeverity.ERROR),
            "warning": sum(1 for i in self.issues if i.severity == HealthSeverity.WARNING),
            "info": sum(1 for i in self.issues if i.severity == HealthSeverity.INFO),
        }


def health_check(mesh: UMesh, *, check_manifold: bool = True) -> HealthReport:
    """对统一模型执行拓扑与几何体检。"""
    report = HealthReport()
    if mesh.n_cells == 0:
        report.add(HealthSeverity.ERROR, "MSH-EMP-001", "模型为空，无单元。")
        return report

    if not np.all(np.isfinite(mesh.vertices)):
        report.add(HealthSeverity.ERROR, "MSH-NAN-001", "存在非有限坐标。")

    volumes = mesh.cell_volumes()
    degenerate = int((volumes <= 1e-12).sum())
    if degenerate:
        report.add(
            HealthSeverity.WARNING, "MSH-DEG-001", f"退化单元 {degenerate} 个（体积近似为 0）。", degenerate
        )

    duplicates = _duplicate_cells(mesh)
    if duplicates:
        report.add(
            HealthSeverity.WARNING, "MSH-DUP-001", f"重复单元 {duplicates} 个。", duplicates
        )

    inverted = _inverted_cells(mesh)
    if inverted:
        report.add(
            HealthSeverity.WARNING,
            "MSH-NRM-001",
            f"反向单元 {inverted} 个（法向/体积朝向不一致）。",
            inverted,
        )

    if check_manifold:
        non_manifold, boundary = _face_statistics(mesh)
        if non_manifold:
            report.add(
                HealthSeverity.ERROR,
                "MSH-NMF-001",
                f"非流形面 {non_manifold} 个（被 3 个及以上单元共享）。",
                non_manifold,
            )
        if boundary:
            report.add(
                HealthSeverity.INFO,
                "MSH-BND-001",
                f"外表面面片 {boundary} 个（体网格边界，正常）。",
                boundary,
            )

    pinched = _pinched_layers(mesh)
    if pinched:
        report.add(
            HealthSeverity.WARNING,
            "MSH-PIN-001",
            f"地层尖灭/无单元: {', '.join(pinched)}。",
            len(pinched),
        )

    inversions = _layer_order_inversions(mesh)
    if inversions:
        report.add(
            HealthSeverity.WARNING,
            "MSH-ORD-001",
            f"层序倒置/超覆单元对 {inversions} 个。",
            inversions,
        )

    holes = _missing_grid_cells(mesh)
    if holes:
        report.add(
            HealthSeverity.WARNING,
            "MSH-HOL-001",
            f"规则网格内部空洞单元 {holes} 个。",
            holes,
        )

    report.metrics["cells"] = float(mesh.n_cells)
    report.metrics["points"] = float(mesh.n_points)
    report.metrics["volume"] = float(mesh.total_volume())
    report.metrics["degenerate"] = float(degenerate)
    report.metrics["inverted"] = float(inverted)
    return report


def _inverted_cells(mesh: UMesh) -> int:
    """统计雅可比为负（法向反向）的六面体单元数。"""
    conn = mesh._conn_array()
    if conn is not None and conn.shape[1] == 8:
        points = mesh.vertices[conn]
        d_xi = ((points[:, 1] + points[:, 2] + points[:, 5] + points[:, 6])
                - (points[:, 0] + points[:, 3] + points[:, 4] + points[:, 7])) / 4.0
        d_eta = ((points[:, 2] + points[:, 3] + points[:, 6] + points[:, 7])
                 - (points[:, 0] + points[:, 1] + points[:, 4] + points[:, 5])) / 4.0
        d_zeta = ((points[:, 4] + points[:, 5] + points[:, 6] + points[:, 7])
                  - (points[:, 0] + points[:, 1] + points[:, 2] + points[:, 3])) / 4.0
        det = np.einsum("ij,ij->i", d_xi, np.cross(d_eta, d_zeta))
        return int((det < 0).sum())

    from geomodel3d.algorithms.repair import _signed_jacobian

    inverted = 0
    for cell in mesh.cells:
        conn_arr = np.asarray(cell, dtype=np.int64)
        if conn_arr.shape[0] == 8 and _signed_jacobian(mesh.vertices[conn_arr]) < 0:
            inverted += 1
    return inverted


def _duplicate_cells(mesh: UMesh) -> int:
    conn = mesh._conn_array()
    if conn is not None:
        keys = np.sort(conn, axis=1)
        return int(conn.shape[0] - _unique_row_count(keys))
    seen: Dict[Tuple[int, ...], int] = {}
    duplicates = 0
    for cell in mesh.cells:
        key = tuple(sorted(int(i) for i in cell))
        if key in seen:
            duplicates += 1
        else:
            seen[key] = 1
    return duplicates


def _unique_row_count(rows: np.ndarray) -> int:
    """对行去重并返回唯一行数（void 视图 + 一维 unique，快于 axis=0）。"""
    rows = np.ascontiguousarray(rows)
    view = rows.view(np.dtype((np.void, rows.dtype.itemsize * rows.shape[1]))).ravel()
    return int(np.unique(view).shape[0])


def _row_counts(rows: np.ndarray) -> np.ndarray:
    """返回每行（去重后）的出现次数。"""
    rows = np.ascontiguousarray(rows)
    view = rows.view(np.dtype((np.void, rows.dtype.itemsize * rows.shape[1]))).ravel()
    _, counts = np.unique(view, return_counts=True)
    return counts


_FACE_INDEX = {
    4: [(0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)],
    5: [(0, 1, 2, 3), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)],
    8: [
        (0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4),
        (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7),
    ],
    6: [(0, 1, 2), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5)],
}


def _cell_faces(conn: np.ndarray):
    n = len(conn)
    faces = _FACE_INDEX.get(n, [tuple(range(n))])
    return [tuple(int(conn[i]) for i in face) for face in faces]


def _face_statistics(mesh: UMesh):
    conn = mesh._conn_array()
    # 仅对“面片顶点数一致”的单元（四面体=3、六面体=4）走向量化路径；
    # 楔体面片有 3/4 顶点混合，np.asarray 会因形状不规则报错，改用标量兜底。
    if conn is not None and conn.shape[1] in (4, 8):
        face_index = np.asarray(_FACE_INDEX[conn.shape[1]], dtype=np.int64)
        faces = conn[:, face_index]
        keys = np.sort(faces, axis=2).reshape(-1, faces.shape[2])
        counts = _row_counts(keys)
        non_manifold = int((counts > 2).sum())
        boundary = int((counts == 1).sum())
        return non_manifold, boundary

    counts_dict: Dict[Tuple[int, ...], int] = {}
    for cell in mesh.cells:
        for face in _cell_faces(cell):
            key = tuple(sorted(face))
            counts_dict[key] = counts_dict.get(key, 0) + 1
    non_manifold = sum(1 for value in counts_dict.values() if value > 2)
    boundary = sum(1 for value in counts_dict.values() if value == 1)
    return non_manifold, boundary


def _pinched_layers(mesh: UMesh) -> List[str]:
    """返回声明了但没有任何单元的地层代码（尖灭/缺失）。"""
    declared = list(mesh.meta.get("layer_codes", []))
    declared.extend(mesh.face_groups)
    seen = set()
    pinched: List[str] = []
    for code in declared:
        if code in seen:
            continue
        seen.add(code)
        indices = mesh.face_groups.get(code)
        if indices is None or len(indices) == 0:
            pinched.append(str(code))
    return pinched


def _layer_order_inversions(mesh: UMesh) -> int:
    """规则网格中，向上层号不应增大；增大即为层序倒置/超覆。"""
    resolution = mesh.meta.get("resolution")
    layer = mesh.cell_attr.get("layer")
    if resolution is None or layer is None:
        return 0
    try:
        nx, ny, nz = (int(v) for v in resolution)
    except (TypeError, ValueError):
        return 0
    values = np.asarray(layer)
    if nx <= 0 or ny <= 0 or nz <= 0 or values.size != nx * ny * nz:
        return 0
    grid = values.reshape(nx, ny, nz)
    return int((np.diff(grid, axis=2) > 0).sum())


def _missing_grid_indices(mesh: UMesh) -> np.ndarray:
    """返回规则体元网格中未与外部连通的空单元索引 ``(K, 3)``。

    仅当 ``meta.resolution`` 与单元中心能对齐到规则网格时启用；非规则网格
    或单元数等于网格总数时返回空数组。索引顺序为 ``(i, j, k)``。
    """
    empty_indices = np.empty((0, 3), dtype=np.int64)
    resolution = mesh.meta.get("resolution")
    if resolution is None or mesh.n_cells == 0:
        return empty_indices
    try:
        nx, ny, nz = (int(v) for v in resolution)
    except (TypeError, ValueError):
        return empty_indices
    total = nx * ny * nz
    if nx <= 0 or ny <= 0 or nz <= 0 or mesh.n_cells >= total:
        return empty_indices

    xmin, xmax, ymin, ymax, zmin, zmax = mesh.bounds()
    if xmax <= xmin or ymax <= ymin or zmax <= zmin:
        return empty_indices
    centers = mesh.cell_centers()
    dx = (xmax - xmin) / nx
    dy = (ymax - ymin) / ny
    dz = (zmax - zmin) / nz
    ix = np.floor((centers[:, 0] - xmin) / dx + 1e-6).astype(np.int64)
    iy = np.floor((centers[:, 1] - ymin) / dy + 1e-6).astype(np.int64)
    iz = np.floor((centers[:, 2] - zmin) / dz + 1e-6).astype(np.int64)
    valid = (
        (ix >= 0) & (ix < nx)
        & (iy >= 0) & (iy < ny)
        & (iz >= 0) & (iz < nz)
    )
    if not valid.all():
        return empty_indices

    occupied = np.zeros((nx, ny, nz), dtype=bool)
    occupied[ix[valid], iy[valid], iz[valid]] = True
    if int(occupied.sum()) != mesh.n_cells:
        return empty_indices
    empty = ~occupied
    if not empty.any():
        return empty_indices

    # 从六个外表面向内洪泛，未被访问的空单元即为内部空洞。
    visited = np.zeros_like(empty, dtype=bool)
    queue = deque()
    boundary = np.zeros_like(empty, dtype=bool)
    boundary[0, :, :] = boundary[-1, :, :] = True
    boundary[:, 0, :] = boundary[:, -1, :] = True
    boundary[:, :, 0] = boundary[:, :, -1] = True
    for seed in np.argwhere(empty & boundary):
        index = (int(seed[0]), int(seed[1]), int(seed[2]))
        visited[index] = True
        queue.append(index)

    while queue:
        i, j, k = queue.popleft()
        for di, dj, dk in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            ni, nj, nk = i + di, j + dj, k + dk
            if 0 <= ni < nx and 0 <= nj < ny and 0 <= nk < nz:
                if empty[ni, nj, nk] and not visited[ni, nj, nk]:
                    visited[ni, nj, nk] = True
                    queue.append((ni, nj, nk))

    return np.argwhere(empty & ~visited).astype(np.int64)


def _missing_grid_cells(mesh: UMesh) -> int:
    return int(len(_missing_grid_indices(mesh)))


__all__ = ["HealthSeverity", "HealthIssue", "HealthReport", "health_check"]
