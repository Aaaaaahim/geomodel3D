"""剖面生成算法（M5 / 6.5）。

- 任意方向 / 水平切面：平面与 UMesh 单元求交，得到各界面/矿体迹线；
- 边界消隐：内部共享边相消，仅保留区域边界；
- 展开为二维剖面坐标（距离 u × 高程 v），同时保留真实大地坐标；
- 钻孔按投影窗口投影并标注分层。
headless 可测，不依赖 Qt。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from geomodel3d.domain.entities import Drillhole, Fault, SectionKind
from geomodel3d.domain.section import (
    SectionBorehole,
    SectionInterval,
    SectionPlan,
    SectionResult,
)
from geomodel3d.engine.umesh import UMesh

_TOL = 1e-7
_ROUND = 1e4


# --------------------------------------------------------------------------- #
# 方案构造
# --------------------------------------------------------------------------- #


def make_arbitrary_section(
    p1: Sequence[float],
    p2: Sequence[float],
    *,
    name: str = "任意剖面",
    bounds: Optional[Sequence[Sequence[float]]] = None,
    crs_epsg: Optional[int] = None,
    offset_window: float = 50.0,
) -> SectionPlan:
    """由两点（平面坐标）构造垂直剖面方案。"""
    a = np.asarray(p1, dtype=np.float64)
    b = np.asarray(p2, dtype=np.float64)
    direction = np.array([b[0] - a[0], b[1] - a[1], 0.0])
    length = float(np.linalg.norm(direction))
    if length < _TOL:
        raise ValueError("剖面线两点不能重合")
    u_axis = direction / length
    origin = np.array([a[0], a[1], 0.0])
    height = 1000.0
    if bounds is not None:
        origin[2] = bounds[0][2]
        height = float(bounds[1][2] - bounds[0][2])
    return SectionPlan(
        name=name,
        kind=SectionKind.ARBITRARY,
        origin=origin.tolist(),
        u_axis=u_axis.tolist(),
        v_axis=[0.0, 0.0, 1.0],
        width=length,
        height=height,
        crs_epsg=crs_epsg,
        borehole_offset_window=offset_window,
    )


def make_horizontal_section(
    elevation: float,
    *,
    name: str = "水平切面",
    bounds: Sequence[Sequence[float]],
    crs_epsg: Optional[int] = None,
) -> SectionPlan:
    """构造水平切面方案。"""
    (xmin, ymin, _), (xmax, ymax, _) = bounds
    return SectionPlan(
        name=name,
        kind=SectionKind.HORIZONTAL,
        origin=[xmin, ymin, float(elevation)],
        u_axis=[1.0, 0.0, 0.0],
        v_axis=[0.0, 1.0, 0.0],
        width=float(xmax - xmin),
        height=float(ymax - ymin),
        crs_epsg=crs_epsg,
        show_boreholes=False,
    )


def make_exploration_sections(
    base_plan: SectionPlan,
    *,
    spacing: float = 200.0,
    count: int = 5,
) -> List[SectionPlan]:
    """在基准剖面两侧按间距生成平行勘探线。"""
    u = np.asarray(base_plan.u_axis, dtype=np.float64)
    normal = np.array([-u[1], u[0], 0.0])
    plans: List[SectionPlan] = []
    start = -((count - 1) / 2.0) * spacing
    for i in range(count):
        shift = start + i * spacing
        plan = base_plan.model_copy(deep=True)
        plan.name = f"{base_plan.name}-{i + 1:02d}"
        plan.origin = (np.asarray(base_plan.origin) + normal * shift).tolist()
        plans.append(plan)
    return plans


# --------------------------------------------------------------------------- #
# 求交
# --------------------------------------------------------------------------- #


def build_section(
    mesh: UMesh,
    plan: SectionPlan,
    *,
    drillholes: Optional[Sequence[Drillhole]] = None,
    faults: Optional[Sequence[Fault]] = None,
) -> SectionResult:
    """在统一模型上生成剖面。"""
    origin = np.asarray(plan.origin, dtype=np.float64)
    u_axis = _unit(plan.u_axis)
    v_axis = _orthonormal(v_axis=plan.v_axis, u_axis=u_axis)
    normal = np.cross(u_axis, v_axis)
    normal = _unit(normal)

    codes = mesh.cell_layer_codes()
    counts_by_layer: Dict[str, Dict[Tuple, int]] = {}
    reps_by_layer: Dict[str, Dict[Tuple, Tuple[np.ndarray, np.ndarray]]] = {}
    show = set(plan.show_layers)

    distances_all = (mesh.vertices - origin) @ normal
    conn = mesh._conn_array()
    if conn is not None:
        cell_distances = distances_all[conn]
        dmax = cell_distances.max(axis=1)
        dmin = cell_distances.min(axis=1)
        candidates = np.nonzero((dmax > _TOL) & (dmin <= _TOL))[0]
    else:
        cell_distances = None
        candidates = np.arange(mesh.n_cells)

    for index in candidates:
        code = codes[index]
        if show and code not in show:
            continue
        if cell_distances is not None:
            points = mesh.vertices[conn[index]]
            cell_d = cell_distances[index]
        else:
            points = mesh.vertices[mesh.cells[index]]
            cell_d = distances_all[mesh.cells[index]]
        polygon = _cell_plane_polygon(points, cell_d, origin, u_axis, v_axis)
        if len(polygon) < 3:
            continue
        counts = counts_by_layer.setdefault(code, {})
        reps = reps_by_layer.setdefault(code, {})
        for i in range(len(polygon)):
            a = polygon[i]
            b = polygon[(i + 1) % len(polygon)]
            key = _edge_key(a, b)
            counts[key] = counts.get(key, 0) + 1
            reps[key] = (a, b)

    result = SectionResult(plan=plan)
    all_points: List[np.ndarray] = []
    for code, counts in counts_by_layer.items():
        reps = reps_by_layer[code]
        segments = [reps[key] for key, count in counts.items() if count % 2 == 1]
        for polyline in _stitch(segments):
            if len(polyline) < 2:
                continue
            uv: List[List[float]] = []
            world: List[List[float]] = []
            for point in polyline:
                u_val = float((point - origin) @ u_axis)
                v_val = float((point - origin) @ v_axis)
                uv.append([round(u_val, 4), round(v_val, 4)])
                world.append((origin + u_val * u_axis + v_val * v_axis).tolist())
            result.polylines.setdefault(code, []).append(uv)
            result.world_polylines.setdefault(code, []).append(world)
            all_points.append(np.asarray(uv, dtype=np.float64))

    if all_points:
        arr = np.vstack(all_points)
        result.bounds_uv = [
            float(arr[:, 0].min()),
            float(arr[:, 1].min()),
            float(arr[:, 0].max()),
            float(arr[:, 1].max()),
        ]
    if plan.show_boreholes and drillholes:
        result.boreholes = _project_boreholes(drillholes, origin, u_axis, v_axis, normal, plan)
    if faults:
        b = mesh.bounds()  # VTK 顺序 [xmin,xmax,ymin,ymax,zmin,zmax]
        box = [b[0], b[2], b[4], b[1], b[3], b[5]]
        for fault in faults:
            trace = _fault_section_trace(fault, origin, u_axis, v_axis, normal, box, plan)
            if len(trace) >= 2:
                result.fault_traces[fault.fault_id] = trace
                result.fault_labels.append(
                    {"fault_id": fault.fault_id, "u": trace[0][0], "v": trace[0][1]}
                )
    return result


def _fault_section_trace(
    fault: Fault,
    origin: np.ndarray,
    u_axis: np.ndarray,
    v_axis: np.ndarray,
    normal: np.ndarray,
    bounds: Sequence[float],
    plan: SectionPlan,
) -> List[List[float]]:
    """计算断层面与剖面平面的交线，并投影为 (u, v) 折线。"""
    params = fault.params
    strike = np.deg2rad(params.strike)
    trace = np.asarray(fault.trace, dtype=np.float64)
    # 走向约定：自正北顺时针；优先用迹线方向
    fallback = np.array([np.sin(strike), np.cos(strike), 0.0])
    if trace.shape[0] >= 2:
        delta = trace[-1, :3] - trace[0, :3]
        delta[2] = 0.0
        length = float(np.linalg.norm(delta))
        strike_dir = delta / length if length > _TOL else fallback
    else:
        strike_dir = fallback
    dip_dir = np.cross(np.array([0.0, 0.0, 1.0]), strike_dir)
    dip_dir_norm = np.linalg.norm(dip_dir)
    if dip_dir_norm < _TOL:
        return []
    dip_dir = dip_dir / dip_dir_norm
    dip = np.deg2rad(params.dip)
    down_dip = dip_dir * np.cos(dip) + np.array([0.0, 0.0, -np.sin(dip)])
    normal_fault = np.cross(strike_dir, down_dip)
    norm = np.linalg.norm(normal_fault)
    if norm < _TOL:
        return []
    normal_fault = normal_fault / norm

    if trace.shape[0] > 0:
        z = trace[:, 2].mean() if trace.shape[1] > 2 else bounds[5]
        center = np.array([trace[:, 0].mean(), trace[:, 1].mean(), z])
    else:
        center = np.array([(bounds[0] + bounds[3]) / 2, (bounds[1] + bounds[4]) / 2, bounds[5]])
    b_fault = float(normal_fault @ center)
    b_section = float(normal @ origin)

    direction_raw = np.cross(normal, normal_fault)
    dd = np.linalg.norm(direction_raw)
    if dd < 1e-9:
        return []
    direction = direction_raw / dd
    # 交线公式需使用“未归一化”的方向向量：p0 = (b1(n2×d) + b2(d×n1)) / |d|²
    point = (
        b_section * np.cross(normal_fault, direction_raw)
        + b_fault * np.cross(direction_raw, normal)
    ) / (dd * dd)

    box_center = np.array(
        [(bounds[0] + bounds[3]) / 2, (bounds[1] + bounds[4]) / 2, (bounds[2] + bounds[5]) / 2]
    )
    # 以“模型中心在交线上的投影”为锚点，避免投影坐标下锚点落在模型外
    anchor = point + float((box_center - point) @ direction) * direction

    diagonal = float(np.linalg.norm(np.asarray(bounds[3:]) - np.asarray(bounds[:3])))
    samples = np.linspace(-diagonal, diagonal, 96)
    points = anchor[None, :] + samples[:, None] * direction[None, :]
    lo = np.asarray(bounds[:3]) - 1e-6
    hi = np.asarray(bounds[3:]) + 1e-6
    inside = ((points >= lo) & (points <= hi)).all(axis=1)
    points = points[inside]
    if points.shape[0] < 2:
        return []
    polyline: List[List[float]] = []
    for p in points:
        u = float((p - origin) @ u_axis)
        v = float((p - origin) @ v_axis)
        if -1e-6 <= u <= plan.width + 1e-6:
            polyline.append([round(u, 4), round(v, 4)])
    return polyline


_HEX_EDGES = [
    (0, 1), (1, 2), (2, 3), (3, 0),
    (4, 5), (5, 6), (6, 7), (7, 4),
    (0, 4), (1, 5), (2, 6), (3, 7),
]
_WEDGE_EDGES = [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3), (0, 3), (1, 4), (2, 5)]
_TETRA_EDGES = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]
_PYRAMID_EDGES = [
    (0, 1), (1, 2), (2, 3), (3, 0),
    (0, 4), (1, 4), (2, 4), (3, 4),
]


def _cell_edges(n: int) -> List[Tuple[int, int]]:
    if n == 8:
        return _HEX_EDGES
    if n == 6:
        return _WEDGE_EDGES
    if n == 5:
        return _PYRAMID_EDGES
    if n == 4:
        return _TETRA_EDGES
    return [(i, j) for i in range(n) for j in range(i + 1, n)]


def _cell_plane_polygon(
    points: np.ndarray,
    distances: np.ndarray,
    origin: np.ndarray,
    u_axis: np.ndarray,
    v_axis: np.ndarray,
) -> List[np.ndarray]:
    # 仅处理位于平面正侧且被平面穿过的单元；纯负侧或完全在平面上的单元跳过，
    # 避免平面恰好与单元面重合时两侧重复生成同一面片。
    if distances.max() <= _TOL:
        return []
    collected: List[np.ndarray] = []
    for i, j in _cell_edges(len(points)):
        di, dj = distances[i], distances[j]
        if abs(di) <= _TOL:
            collected.append(points[i])
        if abs(dj) <= _TOL:
            collected.append(points[j])
        if di * dj < 0:
            t = di / (di - dj)
            collected.append(points[i] + t * (points[j] - points[i]))
    unique = _dedupe(collected)
    if len(unique) < 3:
        return []
    uv = np.array([[(p - origin) @ u_axis, (p - origin) @ v_axis] for p in unique])
    center = uv.mean(axis=0)
    angles = np.arctan2(uv[:, 1] - center[1], uv[:, 0] - center[0])
    order = np.argsort(angles)
    return [unique[i] for i in order]


def _project_boreholes(
    drillholes: Sequence[Drillhole],
    origin: np.ndarray,
    u_axis: np.ndarray,
    v_axis: np.ndarray,
    normal: np.ndarray,
    plan: SectionPlan,
) -> List[SectionBorehole]:
    projected: List[SectionBorehole] = []
    window = plan.borehole_offset_window
    for hole in drillholes:
        collar = np.array([hole.x, hole.y, hole.collar_elev], dtype=np.float64)
        offset = float((collar - origin) @ normal)
        if abs(offset) > window:
            continue
        u = float((collar - origin) @ u_axis)
        intervals = [
            SectionInterval(
                layer_code=interval.layer_code,
                top_v=float(hole.collar_elev - interval.depth_top),
                base_v=float(hole.collar_elev - interval.depth_bottom),
                grade=interval.grade,
            )
            for interval in hole.intervals
        ]
        projected.append(
            SectionBorehole(
                hole_id=hole.hole_id,
                u=u,
                collar_v=float(hole.collar_elev),
                offset=offset,
                intervals=intervals,
            )
        )
    return sorted(projected, key=lambda item: item.u)


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #


def _unit(vector: Sequence[float]) -> np.ndarray:
    array = np.asarray(vector, dtype=np.float64)
    norm = np.linalg.norm(array)
    return array / norm if norm > _TOL else np.array([1.0, 0.0, 0.0])


def _orthonormal(v_axis: Sequence[float], u_axis: np.ndarray) -> np.ndarray:
    v = np.asarray(v_axis, dtype=np.float64)
    v = v - np.dot(v, u_axis) * u_axis
    norm = np.linalg.norm(v)
    if norm < _TOL:
        for candidate in ([0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]):
            vec = np.asarray(candidate) - np.dot(candidate, u_axis) * u_axis
            if np.linalg.norm(vec) > _TOL:
                return vec / np.linalg.norm(vec)
        return np.array([0.0, 0.0, 1.0])
    return v / norm


def _dedupe(points: Sequence[np.ndarray]) -> List[np.ndarray]:
    seen: Dict[Tuple[int, int, int], np.ndarray] = {}
    for point in points:
        key = tuple(int(round(value * _ROUND)) for value in point)
        seen.setdefault(key, point)
    return list(seen.values())


def _edge_key(a: np.ndarray, b: np.ndarray) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
    ka = tuple(int(round(value * _ROUND)) for value in a)
    kb = tuple(int(round(value * _ROUND)) for value in b)
    return (ka, kb) if ka <= kb else (kb, ka)


def _stitch(segments: Sequence[Tuple[np.ndarray, np.ndarray]]) -> List[List[np.ndarray]]:
    remaining = {_edge_key(a, b): (a, b) for a, b in segments}
    polylines: List[List[np.ndarray]] = []
    while remaining:
        key, (a, b) = remaining.popitem()
        chain = [a, b]
        chain = _extend(chain, remaining)
        chain = _extend(list(reversed(chain)), remaining)
        chain = list(reversed(chain))
        polylines.append(chain)
    return polylines


def _extend(chain: List[np.ndarray], remaining: Dict) -> List[np.ndarray]:
    while True:
        tip = chain[-1]
        key_tip = tuple(int(round(value * _ROUND)) for value in tip)
        found = None
        for edge_key, (a, b) in list(remaining.items()):
            ka = tuple(int(round(value * _ROUND)) for value in a)
            kb = tuple(int(round(value * _ROUND)) for value in b)
            if ka == key_tip:
                found = (edge_key, b)
                break
            if kb == key_tip:
                found = (edge_key, a)
                break
        if found is None:
            return chain
        edge_key, nxt = found
        remaining.pop(edge_key)
        chain.append(nxt)


def section_azimuth(plan: SectionPlan) -> float:
    """剖面走向方位角（自正北顺时针，度）。"""
    u = np.asarray(plan.u_axis, dtype=np.float64)
    if abs(u[0]) < _TOL and abs(u[1]) < _TOL:
        return 0.0
    return float(np.degrees(np.arctan2(u[0], u[1])) % 360.0)


__all__ = [
    "build_section",
    "make_arbitrary_section",
    "make_horizontal_section",
    "make_exploration_sections",
    "section_azimuth",
]
