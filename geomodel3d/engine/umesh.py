"""统一模型表示 UMesh（3.1）。

任一引擎产物均归一为 UMesh：顶点 + 单元 + 单元属性 + 面组 + 来源元数据。
可视化、储量、剖面、导出只消费 UMesh，从而与具体引擎解耦。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

# VTK 单元类型（避免在导入期依赖 vtk）
VTK_TRIANGLE = 5
VTK_TETRA = 10
VTK_VOXEL = 11
VTK_HEXAHEDRON = 12
VTK_WEDGE = 13
VTK_PYRAMID = 14


@dataclass
class UMesh:
    """统一三维网格模型。

    Attributes:
        vertices: ``(N, 3)`` float64 顶点坐标。
        cells: 单元连接表，每个元素为顶点索引数组。
        cell_types: ``(M,)`` int，VTK 单元类型。
        cell_attr: 单元属性字典，值为长度 ``M`` 的数组（地层代码/品位/密度等）。
        face_groups: 面组名 -> 单元索引列表（用于导出分组与显隐）。
        meta: 来源元数据（引擎、run_id、CRS、时间等）。
    """

    vertices: np.ndarray
    cells: List[np.ndarray]
    cell_types: np.ndarray
    cell_attr: Dict[str, np.ndarray] = field(default_factory=dict)
    face_groups: Dict[str, List[int]] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)
    version: int = 0

    # ------------------------------------------------------------------ #
    # 构造与校验
    # ------------------------------------------------------------------ #
    def __post_init__(self) -> None:
        self.vertices = np.asarray(self.vertices, dtype=np.float64)
        if self.vertices.ndim != 2 or self.vertices.shape[1] != 3:
            raise ValueError("vertices 必须是 (N, 3) 数组")
        if isinstance(self.cells, np.ndarray) and self.cells.ndim == 2:
            self.cells = np.asarray(self.cells, dtype=np.int64)
            self._conn = self.cells
        else:
            self.cells = [np.asarray(c, dtype=np.int64) for c in self.cells]
            self._conn = None
        self.cell_types = np.asarray(self.cell_types, dtype=np.int32)
        if len(self.cells) != len(self.cell_types):
            raise ValueError("cells 与 cell_types 数量不一致")
        for key, values in self.cell_attr.items():
            array = np.asarray(values)
            if array.shape[0] != len(self.cells):
                raise ValueError(f"单元属性 {key} 长度与单元数不一致")

    def _conn_array(self) -> Optional[np.ndarray]:
        """返回 ``(M, k)`` 统一连接数组；单元长度不一致时返回 ``None``。"""
        if isinstance(self.cells, np.ndarray):
            return self.cells
        if self._conn is None:
            if not self.cells:
                self._conn = None
            else:
                lengths = {len(c) for c in self.cells}
                self._conn = np.vstack(self.cells) if len(lengths) == 1 else None
        return self._conn

    @property
    def n_points(self) -> int:
        return int(self.vertices.shape[0])

    @property
    def n_cells(self) -> int:
        return len(self.cells)

    def bounds(self) -> List[float]:
        if self.n_points == 0:
            return [0.0] * 6
        return [
            float(self.vertices[:, 0].min()),
            float(self.vertices[:, 0].max()),
            float(self.vertices[:, 1].min()),
            float(self.vertices[:, 1].max()),
            float(self.vertices[:, 2].min()),
            float(self.vertices[:, 2].max()),
        ]

    def center(self) -> np.ndarray:
        return self.vertices.mean(axis=0) if self.n_points else np.zeros(3)

    def cell_centers(self) -> np.ndarray:
        if self.n_cells == 0:
            return np.zeros((0, 3))
        conn = self._conn_array()
        if conn is not None:
            return self.vertices[conn].mean(axis=1)
        return np.array([self.vertices[c].mean(axis=0) for c in self.cells])

    def cell_layer_codes(self) -> np.ndarray:
        """返回每个单元的图层代码（优先 face_groups，其次 meta.layer_codes）。"""
        codes = np.empty(self.n_cells, dtype=object)
        if self.face_groups:
            for code, indices in self.face_groups.items():
                if indices:
                    codes[np.asarray(indices, dtype=np.int64)] = code
            return codes
        layer_codes = self.meta.get("layer_codes", [])
        if "layer" in self.cell_attr and layer_codes:
            order = np.asarray(self.cell_attr["layer"], dtype=np.int64)
            table = np.asarray(layer_codes, dtype=object)
            valid = order < len(layer_codes)
            codes[valid] = table[order[valid]]
        return codes

    def cell_extents(self, axis: int = 2) -> np.ndarray:
        """逐单元在某坐标轴上的跨度（默认 Z 向厚度）。"""
        if self.n_cells == 0:
            return np.zeros(0)
        conn = self._conn_array()
        if conn is not None:
            coords = self.vertices[conn][:, :, axis]
            return coords.max(axis=1) - coords.min(axis=1)
        return np.array([np.ptp(self.vertices[c, axis]) for c in self.cells])

    def cell_volumes(self) -> np.ndarray:
        """逐单元体积（四面体分解）。面单元返回 0；规则网格走向量化快路径。"""
        if self.n_cells == 0:
            return np.zeros(0)
        conn = self._conn_array()
        types = np.unique(self.cell_types)
        if conn is not None and types.size == 1:
            cell_type = int(types[0])
            k = conn.shape[1]
            points = self.vertices[conn]
            if cell_type == VTK_TETRA and k == 4:
                return _tetra_volumes_batch(points)
            if cell_type == VTK_HEXAHEDRON and k == 8:
                return _tet_volumes_batch(points, _HEX_TRIS)
            if cell_type == VTK_WEDGE and k == 6:
                return _tet_volumes_batch(points, _WEDGE_TRIS)
            if cell_type == VTK_PYRAMID and k == 5:
                return _tet_volumes_batch(points, _PYRAMID_TRIS)
        volumes = np.zeros(self.n_cells)
        for index, cell in enumerate(self.cells):
            points = self.vertices[cell]
            n = len(points)
            if n == 4:
                volumes[index] = abs(_tetra_volume(points))
            elif n == 8:
                volumes[index] = _polyhedron_volume(points, _HEX_FACES)
            elif n == 6:
                volumes[index] = _polyhedron_volume(points, _WEDGE_FACES)
            elif n == 5:
                volumes[index] = _polyhedron_volume(points, _PYRAMID_FACES)
            else:
                volumes[index] = 0.0
        return volumes

    def total_volume(self) -> float:
        return float(self.cell_volumes().sum())

    def content_hash(self) -> str:
        """基于几何与属性计算内容哈希（用于缓存与回归）。"""
        digest = hashlib.sha256()
        digest.update(np.ascontiguousarray(self.vertices).tobytes())
        for conn in self.cells:
            digest.update(np.ascontiguousarray(conn).tobytes())
        digest.update(self.cell_types.tobytes())
        for key in sorted(self.cell_attr):
            digest.update(key.encode("utf-8"))
            digest.update(np.ascontiguousarray(self.cell_attr[key]).tobytes())
        return digest.hexdigest()

    # ------------------------------------------------------------------ #
    # 与 PyVista / VTK 互转
    # ------------------------------------------------------------------ #
    def to_pyvista(self):
        """转为 ``pyvista.UnstructuredGrid``。"""
        import pyvista as pv

        if self.n_cells == 0:
            return pv.UnstructuredGrid()
        cells = _vtk_cells(self.cells)
        grid = pv.UnstructuredGrid(cells, self.cell_types, self.vertices)
        for key, values in self.cell_attr.items():
            grid.cell_data[key] = np.asarray(values)
        grid.field_data["umesh_version"] = np.array([self.version])
        return grid

    @classmethod
    def from_pyvista(cls, grid, meta: Optional[Dict[str, Any]] = None) -> "UMesh":
        """从 ``pyvista.UnstructuredGrid`` 构造。"""
        import pyvista as pv

        if not isinstance(grid, pv.UnstructuredGrid):
            grid = grid.cast_to_unstructured_grid()
        cell_types = np.asarray(grid.celltypes, dtype=np.int32)
        cells: List[np.ndarray] = []
        raw = np.asarray(grid.cells)
        ptr = 0
        for _ in range(int(grid.n_cells)):
            count = int(raw[ptr])
            ptr += 1
            cells.append(np.asarray(raw[ptr : ptr + count], dtype=np.int64))
            ptr += count
        cell_attr: Dict[str, np.ndarray] = {}
        for key in grid.cell_data:
            if key in ("vtkOriginalPointIds", "vtkOriginalCellIds"):
                continue
            cell_attr[key] = np.asarray(grid.cell_data[key])
        return cls(
            vertices=np.asarray(grid.points, dtype=np.float64),
            cells=cells,
            cell_types=cell_types,
            cell_attr=cell_attr,
            meta=meta or {},
        )

    def copy(self) -> "UMesh":
        cells = self.cells.copy() if isinstance(self.cells, np.ndarray) else [c.copy() for c in self.cells]
        return UMesh(
            vertices=self.vertices.copy(),
            cells=cells,
            cell_types=self.cell_types.copy(),
            cell_attr={k: np.asarray(v).copy() for k, v in self.cell_attr.items()},
            face_groups={k: list(v) for k, v in self.face_groups.items()},
            meta=dict(self.meta),
            version=self.version,
        )


def _vtk_cells(cells: Sequence[np.ndarray]) -> np.ndarray:
    """把单元列表编码为 VTK 的 [n, i0..in-1, n, ...] 连接数组。"""
    flat: List[int] = []
    for conn in cells:
        flat.append(len(conn))
        flat.extend(int(i) for i in conn)
    return np.asarray(flat, dtype=np.int64)


def _tetra_volume(points: np.ndarray) -> float:
    a, b, c, d = points[:4]
    return float(np.dot(np.cross(b - a, c - a), d - a) / 6.0)


# 六面体/楔体的面索引（顶点顺序见 MockEngine._voxel_grid / VTK 约定）
_HEX_FACES = [
    (0, 1, 2, 3),
    (4, 5, 6, 7),
    (0, 1, 5, 4),
    (1, 2, 6, 5),
    (2, 3, 7, 6),
    (3, 0, 4, 7),
]
_WEDGE_FACES = [
    (0, 1, 2),
    (3, 4, 5),
    (0, 1, 4, 3),
    (1, 2, 5, 4),
    (2, 0, 3, 5),
]
_PYRAMID_FACES = [
    (0, 1, 2, 3),
    (0, 1, 4),
    (1, 2, 4),
    (2, 3, 4),
    (3, 0, 4),
]


def _polyhedron_volume(points: np.ndarray, faces) -> float:
    """以形心为顶点、按三角面片分解为四面体求和（对朝向鲁棒）。"""
    centroid = points.mean(axis=0)
    volume = 0.0
    for face in faces:
        for i in range(1, len(face) - 1):
            a = points[face[0]]
            b = points[face[i]]
            c = points[face[i + 1]]
            volume += abs(_tetra_volume(np.array([centroid, a, b, c])))
    return volume


def _triangulate(faces) -> np.ndarray:
    triangles = []
    for face in faces:
        for i in range(1, len(face) - 1):
            triangles.append((face[0], face[i], face[i + 1]))
    return np.asarray(triangles, dtype=np.int64)


_HEX_TRIS = _triangulate(_HEX_FACES)
_WEDGE_TRIS = _triangulate(_WEDGE_FACES)
_PYRAMID_TRIS = _triangulate(_PYRAMID_FACES)


def _tetra_volumes_batch(points: np.ndarray) -> np.ndarray:
    """``(M, 4, 3)`` 四面体批量体积。"""
    a, b, c, d = points[:, 0], points[:, 1], points[:, 2], points[:, 3]
    return np.abs(np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a)) / 6.0


def _tet_volumes_batch(points: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """以形心为公共顶点，按三角面片批量分解求和。``points`` 为 ``(M, k, 3)``。"""
    centroid = points.mean(axis=1, keepdims=True)
    a = points[:, triangles[:, 0], :]
    b = points[:, triangles[:, 1], :]
    c = points[:, triangles[:, 2], :]
    cross = np.cross(b - a, c - a)
    return np.abs(np.einsum("mij,mij->mi", cross, centroid - a)).sum(axis=1) / 6.0


__all__ = [
    "UMesh",
    "VTK_TRIANGLE",
    "VTK_TETRA",
    "VTK_VOXEL",
    "VTK_HEXAHEDRON",
    "VTK_WEDGE",
    "VTK_PYRAMID",
]
