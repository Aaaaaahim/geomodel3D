"""模型智能修复（M6）。

- 顶点焊接：合并容差内的重合顶点，修复网格裂缝；
- 退化/重复单元剔除；
- 拉普拉斯光顺（边界顶点固定），改善锯齿；
产物仍为 UMesh，可无缝回写工程。
headless 可测，不依赖 Qt。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from geomodel3d.engine.umesh import VTK_HEXAHEDRON, VTK_TETRA, UMesh


@dataclass
class RepairOptions:
    weld_tolerance: float = 1e-4
    weld: bool = True
    simplify_tolerance: float = 0.0
    merge_layers: bool = False
    fill_holes: bool = True
    fix_layer_order: bool = False
    remove_degenerate: bool = True
    min_volume: float = 1e-9
    remove_duplicate: bool = True
    fix_normals: bool = True
    smooth_iterations: int = 0
    smooth_factor: float = 0.5


@dataclass
class RepairReport:
    actions: List[str] = field(default_factory=list)
    before_cells: int = 0
    after_cells: int = 0
    before_points: int = 0
    after_points: int = 0

    def add(self, message: str) -> None:
        self.actions.append(message)

    def summary(self) -> str:
        return (
            f"单元 {self.before_cells}→{self.after_cells}，顶点 {self.before_points}→{self.after_points}；"
            + ("；".join(self.actions) if self.actions else "无需修复")
        )


def repair(mesh: UMesh, options: Optional[RepairOptions] = None) -> tuple[UMesh, RepairReport]:
    """执行修复流水线，返回新网格与报告。"""
    options = options or RepairOptions()
    report = RepairReport(before_cells=mesh.n_cells, before_points=mesh.n_points)
    vertices = mesh.vertices.copy()
    cells = [np.asarray(c, dtype=np.int64).copy() for c in mesh.cells]
    cell_types = mesh.cell_types.copy()
    attrs = {key: np.asarray(value).copy() for key, value in mesh.cell_attr.items()}
    face_groups = {code: list(indices) for code, indices in mesh.face_groups.items()}

    if options.fill_holes:
        before = len(cells)
        vertices, cells, cell_types, attrs, face_groups = _fill_grid_holes(
            mesh, vertices, cells, cell_types, attrs, face_groups
        )
        filled = len(cells) - before
        if filled == 0:
            vertices, cells, cell_types, attrs, face_groups, filled = _fill_cavities(
                vertices, cells, cell_types, attrs, face_groups, mesh.meta
            )
        if filled:
            report.add(f"填充内部空洞 {filled} 个")

    if options.fix_layer_order:
        attrs, face_groups, fixed = _fix_layer_order(
            vertices, cells, attrs, face_groups, mesh.meta
        )
        if fixed:
            report.add(f"修正层序倒置 {fixed} 个单元")

    if options.merge_layers:
        before = len(cells)
        vertices, cells, cell_types, attrs, face_groups = _merge_layer_cells(
            vertices, cells, cell_types, attrs, face_groups, mesh.meta
        )
        merged = before - len(cells)
        if merged:
            report.add(f"合并同层单元 {merged} 个")

    keep = np.arange(len(cells), dtype=np.int64)

    if options.weld and vertices.shape[0]:
        vertices, cells, merged = _weld_vertices(vertices, cells, options.weld_tolerance)
        if merged:
            report.add(f"焊接重合顶点 {merged} 个")

    if options.simplify_tolerance > 0 and vertices.shape[0]:
        vertices, cells, simplified = _cluster_vertices(
            vertices, cells, options.simplify_tolerance
        )
        if simplified:
            report.add(f"简化合并顶点 {simplified} 个")

    if options.remove_degenerate:
        keep, removed = _filter_degenerate(vertices, cells, keep, options.min_volume)
        if removed:
            report.add(f"剔除退化单元 {removed} 个")

    if options.remove_duplicate:
        keep, removed = _filter_duplicate(cells, keep)
        if removed:
            report.add(f"剔除重复单元 {removed} 个")

    if options.fix_normals:
        flipped = _fix_normals(vertices, cells, keep)
        if flipped:
            report.add(f"修正反向单元（法向一致性）{flipped} 个")

    if options.smooth_iterations > 0 and vertices.shape[0]:
        vertices = _smooth(vertices, cells, keep, options.smooth_iterations, options.smooth_factor)
        report.add(f"光顺 {options.smooth_iterations} 次")

    kept_cells = [cells[i] for i in keep]
    kept_types = cell_types[keep]
    kept_attrs = {key: value[keep] for key, value in attrs.items()}
    groups = _remap_groups(face_groups, keep)

    new_mesh = UMesh(
        vertices=vertices,
        cells=kept_cells,
        cell_types=kept_types,
        cell_attr=kept_attrs,
        face_groups=groups,
        meta={**mesh.meta, "repaired": True},
        version=mesh.version,
    )
    report.after_cells = new_mesh.n_cells
    report.after_points = new_mesh.n_points
    return new_mesh, report


# --------------------------------------------------------------------------- #
# 规则网格层序修复
# --------------------------------------------------------------------------- #


def _rebuild_layer_groups(attrs: Dict[str, np.ndarray], meta: Dict) -> Dict[str, List[int]]:
    layers = attrs["layer"]
    codes = list(meta.get("layer_codes", []))
    groups: Dict[str, List[int]] = {str(code): [] for code in codes}
    for index, value in enumerate(layers):
        value = int(value)
        code = str(codes[value]) if 0 <= value < len(codes) else str(value)
        groups.setdefault(code, []).append(index)
    return groups


def _merge_layer_cells(
    vertices: np.ndarray,
    cells: List[np.ndarray],
    cell_types: np.ndarray,
    attrs: Dict[str, np.ndarray],
    face_groups: Dict[str, List[int]],
    meta: Dict,
):
    """沿 Z 向合并同层相邻单元（体积守恒的规则网格简化）。

    仅适用于完整规则六面体网格；合并后每列的地层分界保持不变，因此
    逐层体积与总体积严格守恒。
    """
    resolution = meta.get("resolution")
    layer = attrs.get("layer")
    if resolution is None or layer is None or len(cells) == 0:
        return vertices, cells, cell_types, attrs, face_groups
    if any(len(conn) != 8 for conn in cells):
        return vertices, cells, cell_types, attrs, face_groups
    try:
        nx, ny, nz = (int(v) for v in resolution)
    except (TypeError, ValueError):
        return vertices, cells, cell_types, attrs, face_groups
    if nx <= 0 or ny <= 0 or nz <= 0 or len(cells) != nx * ny * nz:
        return vertices, cells, cell_types, attrs, face_groups

    xmin, ymin, zmin = vertices.min(axis=0)
    xmax, ymax, zmax = vertices.max(axis=0)
    dx = (xmax - xmin) / nx
    dy = (ymax - ymin) / ny
    dz = (zmax - zmin) / nz
    if dx <= 0 or dy <= 0 or dz <= 0:
        return vertices, cells, cell_types, attrs, face_groups

    centers = np.array([vertices[conn].mean(axis=0) for conn in cells], dtype=np.float64)
    ix = np.floor((centers[:, 0] - xmin) / dx + 1e-6).astype(np.int64)
    iy = np.floor((centers[:, 1] - ymin) / dy + 1e-6).astype(np.int64)
    iz = np.floor((centers[:, 2] - zmin) / dz + 1e-6).astype(np.int64)
    valid = (
        (ix >= 0) & (ix < nx)
        & (iy >= 0) & (iy < ny)
        & (iz >= 0) & (iz < nz)
    )
    if not valid.all():
        return vertices, cells, cell_types, attrs, face_groups
    index_grid = np.empty((nx, ny, nz), dtype=np.int64)
    index_grid[ix, iy, iz] = np.arange(len(cells), dtype=np.int64)
    occupied = np.zeros((nx, ny, nz), dtype=bool)
    occupied[ix, iy, iz] = True
    if not occupied.all():
        return vertices, cells, cell_types, attrs, face_groups

    vertex_lookup = {_point_key(point): index for index, point in enumerate(vertices)}
    new_vertices: List[np.ndarray] = []

    def vertex_id(point) -> int:
        key = _point_key(point)
        existing = vertex_lookup.get(key)
        if existing is not None:
            return existing
        index = len(vertices) + len(new_vertices)
        vertex_lookup[key] = index
        new_vertices.append(np.asarray(point, dtype=np.float64))
        return index

    new_cells: List[np.ndarray] = []
    new_attrs: Dict[str, List] = {key: [] for key in attrs}
    for i in range(nx):
        for j in range(ny):
            k = 0
            while k < nz:
                start = k
                cell_index = int(index_grid[i, j, k])
                layer_value = int(layer[cell_index])
                while k + 1 < nz:
                    next_index = int(index_grid[i, j, k + 1])
                    if int(layer[next_index]) != layer_value:
                        break
                    k += 1
                x0, x1 = xmin + i * dx, xmin + (i + 1) * dx
                y0, y1 = ymin + j * dy, ymin + (j + 1) * dy
                z0, z1 = zmin + start * dz, zmin + (k + 1) * dz
                conn = np.asarray(
                    [
                        vertex_id((x0, y0, z0)),
                        vertex_id((x1, y0, z0)),
                        vertex_id((x1, y1, z0)),
                        vertex_id((x0, y1, z0)),
                        vertex_id((x0, y0, z1)),
                        vertex_id((x1, y0, z1)),
                        vertex_id((x1, y1, z1)),
                        vertex_id((x0, y1, z1)),
                    ],
                    dtype=np.int64,
                )
                new_cells.append(conn)
                for key, values in attrs.items():
                    new_attrs[key].append(
                        layer_value if key == "layer" else values[cell_index]
                    )
                k += 1

    if new_vertices:
        vertices = np.vstack([vertices, np.asarray(new_vertices, dtype=np.float64)])
    merged_attrs = {
        key: np.asarray(values, dtype=attrs[key].dtype)
        for key, values in new_attrs.items()
    }
    merged_types = np.full(len(new_cells), VTK_HEXAHEDRON, dtype=np.int32)
    return (
        vertices,
        new_cells,
        merged_types,
        merged_attrs,
        _rebuild_layer_groups(merged_attrs, meta),
    )


def _fix_layer_order_regular(
    vertices: np.ndarray,
    cells: List[np.ndarray],
    attrs: Dict[str, np.ndarray],
    meta: Dict,
):
    """规则网格路径；不适用时返回 ``None`` 以触发非规则回退。"""
    resolution = meta.get("resolution")
    layer = attrs.get("layer")
    if resolution is None or layer is None or len(cells) == 0:
        return None
    if not np.issubdtype(np.asarray(layer).dtype, np.integer):
        return None
    try:
        nx, ny, nz = (int(v) for v in resolution)
    except (TypeError, ValueError):
        return None
    if nx <= 0 or ny <= 0 or nz <= 0 or len(cells) != nx * ny * nz:
        return None

    xmin, ymin, _ = vertices.min(axis=0)
    xmax, ymax, _ = vertices.max(axis=0)
    dx = (xmax - xmin) / nx
    dy = (ymax - ymin) / ny
    dz = (vertices[:, 2].max() - vertices[:, 2].min()) / nz
    if dx <= 0 or dy <= 0 or dz <= 0:
        return None

    centers = np.array([vertices[conn].mean(axis=0) for conn in cells], dtype=np.float64)
    ix = np.floor((centers[:, 0] - xmin) / dx + 1e-6).astype(np.int64)
    iy = np.floor((centers[:, 1] - ymin) / dy + 1e-6).astype(np.int64)
    iz = np.floor((centers[:, 2] - vertices[:, 2].min()) / dz + 1e-6).astype(np.int64)
    valid = (
        (ix >= 0) & (ix < nx)
        & (iy >= 0) & (iy < ny)
        & (iz >= 0) & (iz < nz)
    )
    if not valid.all():
        return None

    occupied = np.zeros((nx, ny, nz), dtype=bool)
    occupied[ix, iy, iz] = True
    if not occupied.all():
        return None

    grid = np.empty((nx, ny, nz), dtype=np.asarray(layer).dtype)
    grid[ix, iy, iz] = layer
    fixed_grid = -np.sort(-grid, axis=2)
    changed = int((fixed_grid != grid).sum())
    if changed == 0:
        return attrs, {}, 0

    attrs["layer"] = fixed_grid[ix, iy, iz]
    return attrs, _rebuild_layer_groups(attrs, meta), changed


def _fix_layer_order_columns(
    vertices: np.ndarray,
    cells: List[np.ndarray],
    attrs: Dict[str, np.ndarray],
    face_groups: Dict[str, List[int]],
    meta: Dict,
):
    """非规则/混合网格回退：按 XY 分箱后对每箱按 Z 排序地层号。"""
    layer = attrs.get("layer")
    if layer is None or len(cells) == 0:
        return attrs, face_groups, 0
    if not np.issubdtype(np.asarray(layer).dtype, np.integer):
        return attrs, face_groups, 0

    centers = np.array([vertices[conn].mean(axis=0) for conn in cells], dtype=np.float64)
    resolution = meta.get("resolution")
    if resolution is not None:
        try:
            nxb = max(1, int(resolution[0]))
            nyb = max(1, int(resolution[1]))
        except (TypeError, ValueError, IndexError):
            nxb = nyb = max(1, int(np.ceil(np.sqrt(len(cells)))))
    else:
        nxb = nyb = max(1, int(np.ceil(np.sqrt(len(cells)))))

    xmin, ymin = centers[:, 0].min(), centers[:, 1].min()
    xspan = max(float(centers[:, 0].max() - xmin), 1e-12)
    yspan = max(float(centers[:, 1].max() - ymin), 1e-12)
    ix = np.clip(
        np.floor((centers[:, 0] - xmin) / xspan * nxb).astype(np.int64), 0, nxb - 1
    )
    iy = np.clip(
        np.floor((centers[:, 1] - ymin) / yspan * nyb).astype(np.int64), 0, nyb - 1
    )
    columns: Dict[Tuple[int, int], List[int]] = {}
    for index, key in enumerate(zip(ix, iy)):
        columns.setdefault((int(key[0]), int(key[1])), []).append(index)

    new_layer = np.asarray(layer).copy()
    changed = 0
    for indices in columns.values():
        if len(indices) < 2:
            continue
        ordered = sorted(indices, key=lambda index: -centers[index, 2])
        values = [int(new_layer[index]) for index in ordered]
        target = sorted(values, reverse=True)
        if values != target:
            changed += sum(1 for a, b in zip(values, target) if a != b)
            for index, value in zip(ordered, target):
                new_layer[index] = value

    if changed == 0:
        return attrs, face_groups, 0
    attrs["layer"] = new_layer
    return attrs, _rebuild_layer_groups(attrs, meta), changed


def _fix_layer_order(
    vertices: np.ndarray,
    cells: List[np.ndarray],
    attrs: Dict[str, np.ndarray],
    face_groups: Dict[str, List[int]],
    meta: Dict,
):
    """消除层序倒置/超覆：规则网格优先，非规则网格按 XY 分箱回退。"""
    regular = _fix_layer_order_regular(vertices, cells, attrs, meta)
    if regular is not None:
        fixed_attrs, groups, changed = regular
        if changed == 0:
            return attrs, face_groups, 0
        return fixed_attrs, groups, changed
    return _fix_layer_order_columns(vertices, cells, attrs, face_groups, meta)


# --------------------------------------------------------------------------- #
# 规则网格空洞填充
# --------------------------------------------------------------------------- #


def _point_key(point) -> Tuple[float, float, float]:
    values = np.round(np.asarray(point, dtype=np.float64), 6)
    return float(values[0]), float(values[1]), float(values[2])


def _fill_grid_holes(
    mesh: UMesh,
    vertices: np.ndarray,
    cells: List[np.ndarray],
    cell_types: np.ndarray,
    attrs: Dict[str, np.ndarray],
    face_groups: Dict[str, List[int]],
):
    """在规则体元网格中填充内部空洞，返回更新后的数组与面组。

    仅处理 ``meta.resolution`` 可对齐、且空单元未与外部连通的规则网格。
    新单元几何由网格索引重建，地层取邻域多数层，其它属性复制首个邻域单元。
    """
    from geomodel3d.algorithms.mesh_health import _missing_grid_indices

    holes = _missing_grid_indices(mesh)
    if len(holes) == 0:
        return vertices, cells, cell_types, attrs, face_groups

    original_count = len(cells)
    resolution = mesh.meta.get("resolution")
    if resolution is None:
        return vertices, cells, cell_types, attrs, face_groups
    try:
        nx, ny, nz = (int(v) for v in resolution)
    except (TypeError, ValueError):
        return vertices, cells, cell_types, attrs, face_groups

    xmin, xmax, ymin, ymax, zmin, zmax = mesh.bounds()
    dx = (xmax - xmin) / nx
    dy = (ymax - ymin) / ny
    dz = (zmax - zmin) / nz
    if dx <= 0 or dy <= 0 or dz <= 0:
        return vertices, cells, cell_types, attrs, face_groups

    centers = mesh.cell_centers()
    gi = np.floor((centers[:, 0] - xmin) / dx + 1e-6).astype(np.int64)
    gj = np.floor((centers[:, 1] - ymin) / dy + 1e-6).astype(np.int64)
    gk = np.floor((centers[:, 2] - zmin) / dz + 1e-6).astype(np.int64)
    available: Dict[Tuple[int, int, int], int] = {
        (int(i), int(j), int(k)): index
        for index, (i, j, k) in enumerate(zip(gi, gj, gk))
    }
    layer_attr = attrs.get("layer")
    layer_by_cell: Dict[int, int] = {}
    if layer_attr is not None:
        layer_by_cell = {
            index: int(value) for index, value in enumerate(np.asarray(layer_attr))
        }

    layer_codes = list(mesh.meta.get("layer_codes", []))
    code_for_layer = {index: str(code) for index, code in enumerate(layer_codes)}
    fallback_codes = list(face_groups)

    vertex_lookup = {
        _point_key(point): index for index, point in enumerate(vertices)
    }
    new_vertices: List[np.ndarray] = []

    def vertex_id(point) -> int:
        key = _point_key(point)
        existing = vertex_lookup.get(key)
        if existing is not None:
            return existing
        index = len(vertices) + len(new_vertices)
        vertex_lookup[key] = index
        new_vertices.append(np.asarray(point, dtype=np.float64))
        return index

    offsets = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
    ordered_holes = sorted(
        (tuple(int(v) for v in hole) for hole in holes),
        key=lambda item: -sum(
            (item[0] + di, item[1] + dj, item[2] + dk) in available
            for di, dj, dk in offsets
        ),
    )

    new_types: List[int] = []
    new_attrs: Dict[str, List] = {key: [] for key in attrs}
    for i, j, k in ordered_holes:
        if (i, j, k) in available:
            continue
        x0, x1 = xmin + i * dx, xmin + (i + 1) * dx
        y0, y1 = ymin + j * dy, ymin + (j + 1) * dy
        z0, z1 = zmin + k * dz, zmin + (k + 1) * dz
        conn = np.asarray(
            [
                vertex_id((x0, y0, z0)),
                vertex_id((x1, y0, z0)),
                vertex_id((x1, y1, z0)),
                vertex_id((x0, y1, z0)),
                vertex_id((x0, y0, z1)),
                vertex_id((x1, y0, z1)),
                vertex_id((x1, y1, z1)),
                vertex_id((x0, y1, z1)),
            ],
            dtype=np.int64,
        )

        neighbor_cells = [
            available[key]
            for key in (
                (i + di, j + dj, k + dk) for di, dj, dk in offsets
            )
            if key in available
        ]
        neighbor_layers = [
            layer_by_cell[index] for index in neighbor_cells if index in layer_by_cell
        ]
        layer = Counter(neighbor_layers).most_common(1)[0][0] if neighbor_layers else 0
        preferred = next(
            (
                index
                for index in neighbor_cells
                if index < original_count and layer_by_cell.get(index) == layer
            ),
            None,
        )
        if preferred is None:
            preferred = next(
                (index for index in neighbor_cells if index < original_count),
                None,
            )

        cells.append(conn)
        new_types.append(VTK_HEXAHEDRON)
        for key, values in attrs.items():
            if key == "layer":
                new_attrs[key].append(layer)
            elif preferred is not None:
                new_attrs[key].append(values[preferred])
            else:
                new_attrs[key].append(0)

        cell_index = len(cells) - 1
        available[(i, j, k)] = cell_index
        layer_by_cell[cell_index] = layer
        code = code_for_layer.get(layer)
        if code is None:
            code = fallback_codes[layer] if 0 <= layer < len(fallback_codes) else str(layer)
        face_groups.setdefault(code, []).append(cell_index)

    if new_vertices:
        vertices = np.vstack([vertices, np.asarray(new_vertices, dtype=np.float64)])
    if new_types:
        cell_types = np.concatenate(
            [cell_types, np.asarray(new_types, dtype=np.int32)]
        )
    for key, values in attrs.items():
        if new_attrs[key]:
            attrs[key] = np.concatenate(
                [values, np.asarray(new_attrs[key], dtype=values.dtype)]
            )
    return vertices, cells, cell_types, attrs, face_groups


def _fill_cavities(
    vertices: np.ndarray,
    cells: List[np.ndarray],
    cell_types: np.ndarray,
    attrs: Dict[str, np.ndarray],
    face_groups: Dict[str, List[int]],
    meta: Dict,
    *,
    max_cells: int = 200_000,
):
    """用边界环四面体化填充内部空腔（适用于非规则/混合网格）。

    思路：统计所有单元面，出现一次的面为边界；按共享边把边界面分组，
    最大连通组视为外表面，其余闭合组视为空腔。对每个空腔以其顶点形心
    为公共顶点、边界三角面为底面生成四面体，从而封闭空腔。
    """
    from geomodel3d.algorithms.mesh_health import _cell_faces

    if len(cells) == 0 or len(cells) > max_cells:
        return vertices, cells, cell_types, attrs, face_groups, 0

    face_count: Dict[Tuple[int, ...], int] = {}
    face_owner: Dict[Tuple[int, ...], int] = {}
    face_vertices: Dict[Tuple[int, ...], Tuple[int, ...]] = {}
    for cell_index, conn in enumerate(cells):
        for face in _cell_faces(np.asarray(conn, dtype=np.int64)):
            key = tuple(sorted(int(v) for v in face))
            face_count[key] = face_count.get(key, 0) + 1
            face_owner.setdefault(key, cell_index)
            face_vertices.setdefault(key, tuple(int(v) for v in face))

    boundary_keys = [key for key, count in face_count.items() if count == 1]
    if not boundary_keys:
        return vertices, cells, cell_types, attrs, face_groups, 0

    edge_faces: Dict[Tuple[int, int], List[int]] = {}
    for index, key in enumerate(boundary_keys):
        face = face_vertices[key]
        for i in range(len(face)):
            a, b = face[i], face[(i + 1) % len(face)]
            edge = (a, b) if a <= b else (b, a)
            edge_faces.setdefault(edge, []).append(index)

    visited = set()
    components: List[List[int]] = []
    for start in range(len(boundary_keys)):
        if start in visited:
            continue
        stack = [start]
        component: List[int] = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            face = face_vertices[boundary_keys[current]]
            for i in range(len(face)):
                a, b = face[i], face[(i + 1) % len(face)]
                edge = (a, b) if a <= b else (b, a)
                for neighbor in edge_faces.get(edge, []):
                    if neighbor not in visited:
                        stack.append(neighbor)
        components.append(component)

    if not components:
        return vertices, cells, cell_types, attrs, face_groups, 0

    def triangle_faces(face: Tuple[int, ...]):
        if len(face) == 3:
            return [face]
        if len(face) == 4:
            return [
                (face[0], face[1], face[2]),
                (face[0], face[2], face[3]),
            ]
        return [(face[0], face[i], face[i + 1]) for i in range(1, len(face) - 1)]

    def signed_component_volume(component: List[int]) -> float:
        volume = 0.0
        for index in component:
            key = boundary_keys[index]
            face = face_vertices[key]
            owner = face_owner[key]
            cell_center = vertices[cells[owner]].mean(axis=0)
            face_center = vertices[np.asarray(face, dtype=np.int64)].mean(axis=0)
            for triangle in triangle_faces(face):
                a = vertices[triangle[0]]
                b = vertices[triangle[1]]
                c = vertices[triangle[2]]
                normal = np.cross(b - a, c - a)
                if float(np.dot(normal, face_center - cell_center)) < 0.0:
                    b, c = c, b
                volume += float(np.dot(a, np.cross(b, c))) / 6.0
        return volume

    # 外表面/独立实体的边界面朝外（有向体积为正）；空腔边界面朝内（为负）。
    cavities = [
        component
        for component in components
        if signed_component_volume(component) < -1e-9
    ]
    if not cavities:
        return vertices, cells, cell_types, attrs, face_groups, 0

    layer_attr = attrs.get("layer")
    layer_codes = list(meta.get("layer_codes", []))
    fallback_codes = list(face_groups)
    new_vertices: List[np.ndarray] = []
    new_types: List[int] = []
    new_attrs: Dict[str, List] = {key: [] for key in attrs}
    added = 0

    for component in cavities:
        keys = [boundary_keys[index] for index in component]
        adjacent = [face_owner[key] for key in keys]
        layers = [
            int(layer_attr[index])
            for index in adjacent
            if layer_attr is not None
        ]
        layer = Counter(layers).most_common(1)[0][0] if layers else 0
        preferred = next(
            (
                index
                for index in adjacent
                if layer_attr is not None and int(layer_attr[index]) == layer
            ),
            adjacent[0] if adjacent else None,
        )

        unique_vertices = sorted(
            {int(vertex) for key in keys for vertex in face_vertices[key]}
        )
        centroid = vertices[np.asarray(unique_vertices, dtype=np.int64)].mean(axis=0)
        centroid_index = len(vertices) + len(new_vertices)
        new_vertices.append(np.asarray(centroid, dtype=np.float64))

        code = layer_codes[layer] if 0 <= layer < len(layer_codes) else None
        if code is None:
            code = fallback_codes[layer] if 0 <= layer < len(fallback_codes) else str(layer)

        for key in keys:
            for triangle in triangle_faces(face_vertices[key]):
                conn = np.asarray(
                    [triangle[0], triangle[1], triangle[2], centroid_index],
                    dtype=np.int64,
                )
                cells.append(conn)
                new_types.append(VTK_TETRA)
                for attr_key, values in attrs.items():
                    if attr_key == "layer":
                        new_attrs[attr_key].append(layer)
                    elif preferred is not None:
                        new_attrs[attr_key].append(values[preferred])
                    else:
                        new_attrs[attr_key].append(0)
                face_groups.setdefault(str(code), []).append(len(cells) - 1)
                added += 1

    if new_vertices:
        vertices = np.vstack([vertices, np.asarray(new_vertices, dtype=np.float64)])
    if new_types:
        cell_types = np.concatenate([cell_types, np.asarray(new_types, dtype=np.int32)])
    for key, values in attrs.items():
        if new_attrs[key]:
            attrs[key] = np.concatenate(
                [values, np.asarray(new_attrs[key], dtype=values.dtype)]
            )
    return vertices, cells, cell_types, attrs, face_groups, added


# --------------------------------------------------------------------------- #
# 顶点聚类简化
# --------------------------------------------------------------------------- #


def _cluster_vertices(
    vertices: np.ndarray, cells: List[np.ndarray], tolerance: float
):
    """按空间网格聚类顶点并取簇中心，返回 ``(vertices, cells, 合并数)``。

    这是带误差界的简化：同一 ``tolerance`` 立方体内的顶点被合并，随后由
    退化/重复过滤清理塌缩单元。``tolerance <= 0`` 时不改变输入。
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    if tolerance <= 0 or vertices.shape[0] == 0:
        return vertices, cells, 0

    keys = np.floor(vertices / float(tolerance)).astype(np.int64)
    unique_keys, inverse = np.unique(keys, axis=0, return_inverse=True)
    inverse = np.asarray(inverse).reshape(-1)
    if len(unique_keys) == vertices.shape[0]:
        return vertices, cells, 0

    counts = np.bincount(inverse, minlength=len(unique_keys)).astype(np.float64)
    clustered = np.empty((len(unique_keys), 3), dtype=np.float64)
    for axis in range(3):
        clustered[:, axis] = (
            np.bincount(inverse, weights=vertices[:, axis], minlength=len(unique_keys))
            / counts
        )
    new_cells = [inverse[np.asarray(conn, dtype=np.int64)] for conn in cells]
    return clustered, new_cells, int(vertices.shape[0] - len(unique_keys))


# --------------------------------------------------------------------------- #
# 顶点焊接
# --------------------------------------------------------------------------- #


def _weld_vertices(vertices: np.ndarray, cells: List[np.ndarray], tolerance: float):
    from geomodel3d.algorithms.spatial import pairs_within_tolerance

    pairs = pairs_within_tolerance(vertices, tolerance)
    parent = list(range(len(vertices)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, j in pairs:
        union(i, j)

    mapping = np.empty(len(vertices), dtype=np.int64)
    roots: Dict[int, int] = {}
    for index in range(len(vertices)):
        root = find(index)
        mapping[index] = roots.setdefault(root, len(roots))

    merged = len(vertices) - len(roots)
    new_vertices = np.empty((len(roots), 3), dtype=np.float64)
    for root, new_index in roots.items():
        new_vertices[new_index] = vertices[root]

    new_cells = [mapping[conn] for conn in cells]
    return new_vertices, new_cells, merged


# --------------------------------------------------------------------------- #
# 过滤
# --------------------------------------------------------------------------- #


def _filter_degenerate(vertices, cells, keep, min_volume):
    from geomodel3d.engine.umesh import (
        _HEX_TRIS,
        _WEDGE_TRIS,
        _tet_volumes_batch,
        _tetra_volumes_batch,
    )

    if keep.size == 0:
        return keep, 0
    lengths = {len(cells[int(i)]) for i in keep}
    if len(lengths) == 1:
        conn = np.vstack([np.asarray(cells[int(i)], dtype=np.int64) for i in keep])
        points = vertices[conn]
        repeated = (np.diff(np.sort(conn, axis=1), axis=1) == 0).any(axis=1)
        width = conn.shape[1]
        if width == 4:
            volumes = _tetra_volumes_batch(points)
        elif width == 8:
            volumes = _tet_volumes_batch(points, _HEX_TRIS)
        elif width == 6:
            volumes = _tet_volumes_batch(points, _WEDGE_TRIS)
        else:
            volumes = None
        if volumes is not None:
            bad = repeated | (volumes <= min_volume)
            survivors = keep[~bad].astype(np.int64)
            return survivors, int(bad.sum())

    survivors: List[int] = []
    removed = 0
    for index in keep:
        conn = cells[index]
        points = vertices[conn]
        if len(set(int(i) for i in conn)) < len(conn):
            removed += 1
            continue
        volume = _cell_volume(points)
        if volume <= min_volume:
            removed += 1
        else:
            survivors.append(int(index))
    return np.asarray(survivors, dtype=np.int64), removed


def _cell_volume(points: np.ndarray) -> float:
    from geomodel3d.engine.umesh import (
        _HEX_FACES,
        _PYRAMID_FACES,
        _WEDGE_FACES,
        _polyhedron_volume,
        _tetra_volume,
    )

    n = len(points)
    if n == 4:
        return abs(_tetra_volume(points))
    if n == 8:
        return _polyhedron_volume(points, _HEX_FACES)
    if n == 6:
        return _polyhedron_volume(points, _WEDGE_FACES)
    if n == 5:
        return _polyhedron_volume(points, _PYRAMID_FACES)
    return 1.0


def _filter_duplicate(cells, keep):
    seen = set()
    survivors: List[int] = []
    removed = 0
    for index in keep:
        key = tuple(sorted(int(i) for i in cells[index]))
        if key in seen:
            removed += 1
        else:
            seen.add(key)
            survivors.append(int(index))
    return np.asarray(survivors, dtype=np.int64), removed


_ORIENTATION_FLIP = {
    8: [0, 3, 2, 1, 4, 7, 6, 5],
}


def _signed_jacobian(points: np.ndarray) -> float:
    """三线性六面体中心点雅可比行列式符号（>0 为右旋/正朝向）。"""
    p = points
    d_xi = ((p[1] + p[2] + p[5] + p[6]) - (p[0] + p[3] + p[4] + p[7])) / 4.0
    d_eta = ((p[2] + p[3] + p[6] + p[7]) - (p[0] + p[1] + p[4] + p[5])) / 4.0
    d_zeta = ((p[4] + p[5] + p[6] + p[7]) - (p[0] + p[1] + p[2] + p[3])) / 4.0
    return float(np.dot(d_xi, np.cross(d_eta, d_zeta)))


def _fix_normals(vertices, cells, keep) -> int:
    """翻转反向六面体，保持一致的右手朝向（BR-09 法向一致性）。"""
    hexes = [int(i) for i in keep if len(cells[int(i)]) == 8]
    if not hexes:
        return 0
    conn = np.vstack([np.asarray(cells[i], dtype=np.int64) for i in hexes])
    points = vertices[conn]
    d_xi = ((points[:, 1] + points[:, 2] + points[:, 5] + points[:, 6])
            - (points[:, 0] + points[:, 3] + points[:, 4] + points[:, 7])) / 4.0
    d_eta = ((points[:, 2] + points[:, 3] + points[:, 6] + points[:, 7])
             - (points[:, 0] + points[:, 1] + points[:, 4] + points[:, 5])) / 4.0
    d_zeta = ((points[:, 4] + points[:, 5] + points[:, 6] + points[:, 7])
              - (points[:, 0] + points[:, 1] + points[:, 2] + points[:, 3])) / 4.0
    det = np.einsum("ij,ij->i", d_xi, np.cross(d_eta, d_zeta))
    order = np.asarray(_ORIENTATION_FLIP[8], dtype=np.int64)
    flipped = 0
    for local in np.nonzero(det < 0)[0]:
        index = hexes[int(local)]
        cells[index] = np.asarray(cells[index], dtype=np.int64)[order]
        flipped += 1
    return flipped


def _remap_groups(face_groups: Dict[str, List[int]], keep: np.ndarray) -> Dict[str, List[int]]:
    inverse = {int(old): new for new, old in enumerate(keep)}
    groups: Dict[str, List[int]] = {}
    for code, indices in face_groups.items():
        remapped = [inverse[int(i)] for i in indices if int(i) in inverse]
        if remapped:
            groups[code] = remapped
    return groups


# --------------------------------------------------------------------------- #
# 光顺
# --------------------------------------------------------------------------- #


def _smooth(vertices, cells, keep, iterations, factor):
    neighbors: Dict[int, set] = {}
    for index in keep:
        conn = [int(i) for i in cells[index]]
        for i in conn:
            for j in conn:
                if i != j:
                    neighbors.setdefault(i, set()).add(j)

    bounds = vertices.min(axis=0), vertices.max(axis=0)
    fixed = np.any(
        (np.isclose(vertices, bounds[0], atol=1e-9) | np.isclose(vertices, bounds[1], atol=1e-9)),
        axis=1,
    )

    result = vertices.copy()
    for _ in range(iterations):
        updated = result.copy()
        for index, adjacent in neighbors.items():
            if fixed[index] or not adjacent:
                continue
            centroid = result[list(adjacent)].mean(axis=0)
            updated[index] = result[index] + factor * (centroid - result[index])
        result = updated
    return result


__all__ = ["RepairOptions", "RepairReport", "repair"]
