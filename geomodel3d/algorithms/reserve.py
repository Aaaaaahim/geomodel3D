"""储量计算算法（M4 / 6.4）。

- 体积法：封闭实体逐单元体积求和 -> 矿石量/金属量；
- 块段法：规则块段聚合，按工业指标（厚度/品位/夹石）取舍；
- 分级：按与最近钻孔的平面距离套用探明/控制/推断口径（BR-05 可配）。
headless 可测，不依赖 Qt。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from geomodel3d.algorithms.interpolation import (
    dsi,
    ensure_interp_method,
    idw,
    kriging,
    recommended_k,
)
from geomodel3d.config.schema import InterpConfig
from geomodel3d.domain.entities import Drillhole, ReserveClass
from geomodel3d.domain.reserve import (
    ReserveBlock,
    ReserveClassSummary,
    ReserveMetrics,
    ReserveResult,
)
from geomodel3d.engine.umesh import UMesh


def estimate_reserves(
    mesh: UMesh,
    metrics: ReserveMetrics,
    *,
    drillholes: Optional[Sequence[Drillhole]] = None,
    interp: Optional[InterpConfig] = None,
    body_id: str = "ore_body",
) -> ReserveResult:
    """对统一模型中的矿体执行储量估算。"""
    drillholes = list(drillholes or [])
    interp = interp or InterpConfig()
    result = ReserveResult(body_id=body_id, method=metrics.method, metrics=metrics)

    if not metrics.body_layers:
        result.warnings.append("未指定矿体地层（body_layers 为空），不计算储量。")
        return result

    codes = mesh.cell_layer_codes()
    mask = _body_mask(codes, metrics.body_layers)
    if not mask.any():
        result.warnings.append("未找到匹配矿体地层的单元。")
        return result

    volumes = mesh.cell_volumes()
    centers = mesh.cell_centers()
    thickness = _cell_thickness(mesh)
    grades = _cell_grades(mesh, centers, drillholes, metrics, interp, result)

    idx = np.nonzero(mask)[0]
    selected = _apply_indicators(idx, grades, thickness, metrics)
    if selected.size and metrics.max_waste_thickness > 0:
        selected = _apply_waste_removal(idx, selected, centers, thickness, metrics)
    if selected.size == 0:
        result.warnings.append("按工业指标筛选后无可采单元。")
        return result

    if metrics.method == "block":
        blocks = _block_aggregate(
            selected, centers, volumes, thickness, grades, codes, metrics, drillholes
        )
    else:
        blocks = _cell_aggregate(
            selected, centers, volumes, thickness, grades, codes, metrics, drillholes
        )

    _finalize(result, blocks, metrics)
    return result


# --------------------------------------------------------------------------- #
# 单元属性
# --------------------------------------------------------------------------- #


def _cell_layer_codes(mesh: UMesh) -> np.ndarray:
    """兼容旧调用，转发到 :meth:`UMesh.cell_layer_codes`。"""
    return mesh.cell_layer_codes()


def _body_mask(codes: np.ndarray, body_layers: Sequence[str]) -> np.ndarray:
    if not body_layers:
        return np.ones(len(codes), dtype=bool)
    wanted = set(body_layers)
    return np.array([code in wanted for code in codes], dtype=bool)


def _cell_thickness(mesh: UMesh) -> np.ndarray:
    return mesh.cell_extents(axis=2)


def _cell_grades(
    mesh: UMesh,
    centers: np.ndarray,
    drillholes: Sequence[Drillhole],
    metrics: ReserveMetrics,
    interp: InterpConfig,
    result: ReserveResult,
) -> np.ndarray:
    if "grade" in mesh.cell_attr:
        return np.asarray(mesh.cell_attr["grade"], dtype=np.float64)

    ensure_interp_method(interp.method)
    points, values = _grade_samples(drillholes, metrics.body_layers)
    if len(points) == 0:
        result.warnings.append("无可用品位样本，品位按 0 处理。")
        return np.zeros(mesh.n_cells)
    if len(points) >= 4 and interp.method in ("kriging", "cokriging"):
        grades = kriging(
            points,
            values,
            centers,
            variogram=interp.variogram_model,
            nugget=interp.nugget,
            sill=interp.sill,
            range_=interp.range_,
            k=recommended_k(len(points), interp.neighbor),
        )
    elif interp.method == "dsi":
        grades = dsi(
            points,
            values,
            centers,
            k=recommended_k(len(points), interp.neighbor),
            iterations=20,
        )
    else:
        grades = idw(points, values, centers, k=recommended_k(len(points), interp.neighbor))
    return np.nan_to_num(grades, nan=0.0)


def _grade_samples(
    drillholes: Sequence[Drillhole], body_layers: Sequence[str]
) -> Tuple[np.ndarray, np.ndarray]:
    wanted = set(body_layers)
    points: List[List[float]] = []
    values: List[float] = []
    for hole in drillholes:
        weighted = 0.0
        weight = 0.0
        weighted_depth = 0.0
        for interval in hole.intervals:
            if wanted and interval.layer_code not in wanted:
                continue
            if interval.grade is None:
                continue
            thick = interval.thickness or (interval.depth_bottom - interval.depth_top)
            mid = (interval.depth_top + interval.depth_bottom) / 2.0
            weighted += interval.grade * thick
            weighted_depth += mid * thick
            weight += thick
        if weight > 0:
            points.append([hole.x, hole.y, hole.collar_elev - weighted_depth / weight])
            values.append(weighted / weight)
    return np.asarray(points, dtype=np.float64), np.asarray(values, dtype=np.float64)


# --------------------------------------------------------------------------- #
# 工业指标与块段
# --------------------------------------------------------------------------- #


def _apply_indicators(
    idx: np.ndarray,
    grades: np.ndarray,
    thickness: np.ndarray,
    metrics: ReserveMetrics,
) -> np.ndarray:
    if idx.size == 0:
        return idx
    mask = (grades[idx] >= metrics.cutoff_grade) & (thickness[idx] >= metrics.min_thickness)
    return idx[mask]


def _apply_waste_removal(
    body_idx: np.ndarray,
    selected: np.ndarray,
    centers: np.ndarray,
    thickness: np.ndarray,
    metrics: ReserveMetrics,
) -> np.ndarray:
    """夹石剔除（BR-04）：按纵向单元序列识别矿体内部的废石夹层。

    对每一垂直柱，找出被矿化单元夹持（上下均为可采单元）的连续废石段：

    - 段厚 ≥ ``max_waste_thickness``：作为夹石剔除，不参与体积/矿量累计；
    - 段厚 <  ``max_waste_thickness``：并入邻段（贫化），计入矿量。

    该规则仅在 ``max_waste_thickness > 0`` 时启用。
    """
    threshold = float(metrics.max_waste_thickness)
    if threshold <= 0 or body_idx.size == 0:
        return selected

    body_idx = np.asarray(body_idx, dtype=np.int64)
    coords = centers[body_idx]
    selected_set = {int(i) for i in np.asarray(selected, dtype=np.int64)}
    is_ore = np.array([int(i) in selected_set for i in body_idx], dtype=bool)

    columns: Dict[Tuple[float, float], List[int]] = {}
    for local, (x, y) in enumerate(coords[:, :2]):
        key = (round(float(x), 6), round(float(y), 6))
        columns.setdefault(key, []).append(local)

    include = np.zeros(body_idx.size, dtype=bool)
    for members in columns.values():
        ordered = sorted(members, key=lambda pos: -float(coords[pos, 2]))
        count = len(ordered)
        start = 0
        while start < count:
            if is_ore[ordered[start]]:
                start += 1
                continue
            end = start
            while end < count and not is_ore[ordered[end]]:
                end += 1
            internal = start > 0 and end < count
            if internal:
                run_cells = body_idx[np.asarray(ordered[start:end], dtype=np.int64)]
                run_thickness = float(thickness[run_cells].sum())
                if run_thickness < threshold:
                    include[np.asarray(ordered[start:end], dtype=np.int64)] = True
            start = end

    if not include.any():
        return selected
    return np.union1d(np.asarray(selected, dtype=np.int64), body_idx[include])


def _classify_array(distances: np.ndarray, metrics: ReserveMetrics) -> np.ndarray:
    classes = np.full(len(distances), ReserveClass.INFERRED.value, dtype=object)
    classes[distances <= metrics.indicated_distance] = ReserveClass.INDICATED.value
    classes[distances <= metrics.measured_distance] = ReserveClass.MEASURED.value
    return classes


def _cell_aggregate(
    idx, centers, volumes, thickness, grades, codes, metrics, drillholes
) -> List[ReserveBlock]:
    """体积法：按（地层 × 分级 × 中段）聚合，避免逐单元产生海量块段。"""
    distances = _nearest_distances(centers[idx], drillholes)
    classes = _classify_array(distances, metrics)
    levels = np.floor(centers[idx, 2] / metrics.level_height) * metrics.level_height
    groups: Dict[Tuple[str, str, float], List[int]] = {}
    for position, index in enumerate(idx):
        key = (str(codes[index]), classes[position], float(levels[position]))
        groups.setdefault(key, []).append(int(index))

    blocks: List[ReserveBlock] = []
    for order, ((code, class_value, level), members) in enumerate(groups.items()):
        member = np.asarray(members, dtype=np.int64)
        volume = float(volumes[member].sum())
        thick = float(
            np.average(thickness[member], weights=np.maximum(volumes[member], 1e-9))
        )
        grade = float(np.average(grades[member], weights=np.maximum(volumes[member], 1e-9)))
        center = centers[member].mean(axis=0)
        distance = float(_nearest_distances(center[None, :], drillholes)[0])
        blocks.append(
            ReserveBlock(
                block_id=f"V{order:05d}",
                center=[float(v) for v in center],
                volume=volume,
                tonnage=_tonnage(volume, metrics),
                grade=grade,
                thickness=thick,
                reserve_class=ReserveClass(class_value),
                layer_code=code,
                level=level,
                drillhole_distance=distance,
            )
        )
    return blocks


def _block_aggregate(
    idx, centers, volumes, thickness, grades, codes, metrics, drillholes
) -> List[ReserveBlock]:
    size = np.asarray(metrics.block_size, dtype=np.float64)
    origin = centers[idx].min(axis=0)
    keys = np.floor((centers[idx] - origin) / size).astype(np.int64)
    unique_keys, inverse = np.unique(keys, axis=0, return_inverse=True)
    inverse = np.asarray(inverse).reshape(-1)
    order = np.argsort(inverse, kind="stable")
    sorted_inverse = inverse[order]
    sorted_idx = idx[order]
    boundaries = np.searchsorted(sorted_inverse, np.arange(len(unique_keys) + 1))

    blocks: List[ReserveBlock] = []
    for group in range(len(unique_keys)):
        member = sorted_idx[boundaries[group] : boundaries[group + 1]]
        volume = float(volumes[member].sum())
        thick = float(
            np.average(thickness[member], weights=np.maximum(volumes[member], 1e-9))
        )
        grade = float(np.average(grades[member], weights=np.maximum(volumes[member], 1e-9)))
        center = centers[member].mean(axis=0)
        if grade < metrics.cutoff_grade or thick < metrics.min_thickness:
            continue
        distance = _nearest_distance(center, drillholes)
        layer = _majority([codes[i] for i in member])
        blocks.append(
            ReserveBlock(
                block_id=f"K{group:05d}",
                center=[float(v) for v in center],
                volume=volume,
                tonnage=_tonnage(volume, metrics),
                grade=grade,
                thickness=thick,
                reserve_class=_classify(distance, metrics),
                layer_code=layer,
                level=float(np.floor(center[2] / metrics.level_height) * metrics.level_height),
                drillhole_distance=distance,
            )
        )
    return blocks


def _tonnage(volume: float, metrics: ReserveMetrics) -> float:
    return float(volume) * metrics.density * (1.0 - metrics.moisture)


def _nearest_distance(point: np.ndarray, drillholes: Sequence[Drillhole]) -> float:
    if not drillholes:
        return float("inf")
    coords = np.array([[hole.x, hole.y] for hole in drillholes], dtype=np.float64)
    return float(np.sqrt(((coords - point[:2]) ** 2).sum(axis=1).min()))


def _nearest_distances(points: np.ndarray, drillholes: Sequence[Drillhole]) -> np.ndarray:
    """批量计算点到最近钻孔的平面距离。"""
    if len(points) == 0:
        return np.zeros(0)
    if not drillholes:
        return np.full(len(points), np.inf)
    from geomodel3d.algorithms.spatial import nearest_distances

    coords = np.array([[hole.x, hole.y] for hole in drillholes], dtype=np.float64)
    return nearest_distances(coords, np.asarray(points)[:, :2])


def _classify(distance: float, metrics: ReserveMetrics) -> ReserveClass:
    if distance <= metrics.measured_distance:
        return ReserveClass.MEASURED
    if distance <= metrics.indicated_distance:
        return ReserveClass.INDICATED
    return ReserveClass.INFERRED


def _majority(values: List[str]) -> str:
    if not values:
        return ""
    counts: Dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return max(counts, key=counts.get)


def _grade_to_fraction(grade: float, unit: str) -> float:
    if unit == "%":
        return grade / 100.0
    if unit.lower() in ("g/t", "gt", "ppm"):
        return grade / 1e6
    return grade


def _finalize(result: ReserveResult, blocks: List[ReserveBlock], metrics: ReserveMetrics) -> None:
    result.blocks = blocks
    if not blocks:
        return
    fractions = np.array([_grade_to_fraction(b.grade, metrics.grade_unit) for b in blocks])
    tonnages = np.array([b.tonnage for b in blocks])
    volumes = np.array([b.volume for b in blocks])
    thicknesses = np.array([b.thickness for b in blocks])
    metals = tonnages * fractions * metrics.recovery

    result.total_volume = float(volumes.sum())
    result.total_tonnage = float(tonnages.sum())
    result.total_metal = float(metals.sum())
    result.avg_grade = float(np.average([b.grade for b in blocks], weights=np.maximum(tonnages, 1e-9)))
    result.avg_thickness = float(np.average(thicknesses, weights=np.maximum(volumes, 1e-9)))

    by_class: Dict[str, ReserveClassSummary] = {}
    levels: Dict[float, Dict[str, float]] = {}
    for block, metal in zip(blocks, metals):
        key = block.reserve_class.value
        summary = by_class.setdefault(
            key, ReserveClassSummary(reserve_class=block.reserve_class)
        )
        summary.block_count += 1
        summary.volume += block.volume
        summary.tonnage += block.tonnage
        summary.metal += float(metal)

        level = levels.setdefault(block.level, {"volume": 0.0, "tonnage": 0.0, "metal": 0.0})
        level["volume"] += block.volume
        level["tonnage"] += block.tonnage
        level["metal"] += float(metal)

    for summary in by_class.values():
        if summary.tonnage > 0:
            fraction = summary.metal / summary.tonnage / max(metrics.recovery, 1e-9)
            summary.avg_grade = _fraction_to_grade(fraction, metrics.grade_unit)
        members = [b for b in blocks if b.reserve_class == summary.reserve_class]
        if members:
            weights = np.maximum(np.array([b.volume for b in members]), 1e-9)
            summary.avg_thickness = float(
                np.average([b.thickness for b in members], weights=weights)
            )
    result.by_class = by_class
    result.by_level = [
        {"level": level, **values} for level, values in sorted(levels.items(), reverse=True)
    ]


def _fraction_to_grade(fraction: float, unit: str) -> float:
    if unit == "%":
        return fraction * 100.0
    if unit.lower() in ("g/t", "gt", "ppm"):
        return fraction * 1e6
    return fraction


__all__ = ["estimate_reserves"]
