"""开源断层几何切割预览（VTK clip）。

说明：OpenGeode 的拓扑布尔切割/DSI 属于商业扩展。本模块提供基于 VTK
的几何切割替代：沿断层面把模型裁成两盘，按落差平移一盘后重新合并。
两盘各自保持封闭；切割面与单元边界重合时会自动微移重试并清理退化
单元。它不是 OpenGeode 原生布尔实体，但可作为水密几何切割预览；
体积、储量与剖面仍按单元几何计算。
"""
from __future__ import annotations

import logging
from typing import Tuple

import numpy as np

from geomodel3d.config.errors import ErrorCode, GeoModelError
from geomodel3d.domain.entities import Fault, FaultSide
from geomodel3d.engine.umesh import UMesh

logger = logging.getLogger(__name__)


def fault_plane(fault: Fault) -> Tuple[np.ndarray, np.ndarray]:
    """返回断层面法向与一个面上的点（世界坐标）。"""
    trace = np.asarray(fault.trace, dtype=np.float64)
    if trace.shape[0] > 0:
        center = trace[:, :3].mean(axis=0)
    else:
        center = np.zeros(3)

    strike = np.deg2rad(fault.params.strike)
    fallback_along = np.array([np.sin(strike), np.cos(strike), 0.0])
    if trace.shape[0] >= 2:
        delta = trace[-1, :3] - trace[0, :3]
        delta[2] = 0.0
        norm = float(np.linalg.norm(delta))
        along = delta / norm if norm > 1e-9 else fallback_along
    else:
        along = fallback_along

    dip_dir = np.cross(np.array([0.0, 0.0, 1.0]), along)
    dip_norm = float(np.linalg.norm(dip_dir))
    if dip_norm < 1e-9:
        dip_dir = np.array([0.0, 1.0, 0.0])
    else:
        dip_dir = dip_dir / dip_norm
    dip = np.deg2rad(fault.params.dip)
    down_dip = dip_dir * np.cos(dip) + np.array([0.0, 0.0, -np.sin(dip)])

    normal = np.cross(along, down_dip)
    norm = float(np.linalg.norm(normal))
    if norm < 1e-9:
        normal = np.array([0.0, 1.0, 0.0])
    else:
        normal = normal / norm
    return normal, center


def _displacement(fault: Fault) -> np.ndarray:
    """正侧位移向量：水平落差按断层性质，垂直落差向下。"""
    strike = np.deg2rad(fault.params.strike)
    fallback_along = np.array([np.sin(strike), np.cos(strike), 0.0])
    trace = np.asarray(fault.trace, dtype=np.float64)
    if trace.shape[0] >= 2:
        delta = trace[-1, :3] - trace[0, :3]
        delta[2] = 0.0
        norm = float(np.linalg.norm(delta))
        along = delta / norm if norm > 1e-9 else fallback_along
    else:
        along = fallback_along

    normal = np.cross(np.array([0.0, 0.0, 1.0]), along)
    norm = float(np.linalg.norm(normal))
    normal = normal / norm if norm > 1e-9 else np.array([0.0, 1.0, 0.0])

    if fault.side == FaultSide.STRIKE_SLIP:
        direction, sign = along, 1.0
    elif fault.side == FaultSide.REVERSE:
        direction, sign = normal, -1.0
    else:
        direction, sign = normal, 1.0

    horizontal = direction[:2] * float(fault.params.throw_h) * sign
    return np.array([horizontal[0], horizontal[1], -float(fault.params.throw_v)])


def _cut_once(grid, normal, origin, displacement):
    positive = grid.clip(
        normal=normal, origin=origin, invert=False, crinkle=False
    )
    negative = grid.clip(
        normal=normal, origin=origin, invert=True, crinkle=False
    )
    if positive.n_cells == 0 or negative.n_cells == 0:
        return None
    positive = positive.translate(displacement, inplace=False)
    return positive.merge(negative).clean()


def _finalize(merged, mesh: UMesh, fault: Fault, normal, origin, displacement) -> UMesh:
    meta = dict(mesh.meta)
    meta.pop("layer_surfaces", None)
    meta["engine"] = "vtk_fault_cut"
    meta["fault_cut"] = {
        "fault_id": fault.fault_id,
        "normal": [float(v) for v in normal],
        "origin": [float(v) for v in origin],
        "displacement": [float(v) for v in displacement],
        "watertight": False,
    }
    result = UMesh.from_pyvista(merged, meta=meta)
    result.version = mesh.version + 1

    codes = list(mesh.meta.get("layer_codes", []))
    groups = {str(code): [] for code in codes}
    for index, value in enumerate(result.cell_attr.get("layer", [])):
        value = int(value)
        code = str(codes[value]) if 0 <= value < len(codes) else str(value)
        groups.setdefault(code, []).append(index)
    result.face_groups = groups
    return result


def cut_mesh_by_fault(mesh: UMesh, fault: Fault) -> UMesh:
    """沿断层面切割并平移一盘，返回尽量水密的 UMesh。

    切割面恰好落在单元边界时，VTK 可能产生退化单元；这里会沿法向做
    微小偏移重试，并在必要时清理退化/重复单元。结果是两盘分离的封闭
    预览，不是 OpenGeode 原生布尔实体。
    """
    if mesh.n_cells == 0:
        raise GeoModelError(ErrorCode.MDL_CUT_001, "模型为空，无法执行断层切割。")
    try:
        import pyvista as pv
    except Exception as exc:  # noqa: BLE001
        raise GeoModelError(
            ErrorCode.MDL_CUT_001,
            "执行几何切割预览需要 pyvista/VTK。",
        ) from exc

    grid = mesh.to_pyvista()
    if not isinstance(grid, pv.UnstructuredGrid):
        grid = grid.cast_to_unstructured_grid()
    normal, origin = fault_plane(fault)
    displacement = _displacement(fault)

    bounds = mesh.bounds()
    diagonal = float(
        np.linalg.norm(
            np.asarray(bounds[1::2], dtype=float)
            - np.asarray(bounds[0::2], dtype=float)
        )
    )
    epsilon = max(diagonal * 1e-6, 1e-6)
    offsets = (0.0, epsilon, -epsilon, 4.0 * epsilon, -4.0 * epsilon)

    best = None
    best_degenerate = None
    last_error = None
    for offset in offsets:
        cut_origin = origin + normal * offset
        try:
            merged = _cut_once(grid, normal, cut_origin, displacement)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
        if merged is None:
            continue
        candidate = _finalize(
            merged, mesh, fault, normal, cut_origin, displacement
        )
        degenerate = int((candidate.cell_volumes() <= 1e-12).sum())
        if best_degenerate is None or degenerate < best_degenerate:
            best, best_degenerate = candidate, degenerate
        if degenerate == 0:
            break

    if best is None:
        raise GeoModelError(
            ErrorCode.MDL_CUT_001,
            "断层面未把模型分成两盘，请检查断层位置/走向/倾角。",
        ) from last_error

    if best_degenerate:
        from geomodel3d.algorithms.repair import RepairOptions, repair

        repaired, _ = repair(
            best,
            RepairOptions(
                fill_holes=False,
                weld=True,
                remove_degenerate=True,
                remove_duplicate=True,
                fix_normals=True,
            ),
        )
        repaired.meta.update(best.meta)
        repaired.meta["fault_cut"]["repaired"] = True
        best = repaired

    from geomodel3d.algorithms.mesh_health import health_check

    report = health_check(best)
    degenerate = int((best.cell_volumes() <= 1e-12).sum())
    best.meta["fault_cut"]["watertight"] = bool(report.passed and degenerate == 0)
    logger.info(
        "断层几何切割完成: %s -> %d 单元（watertight=%s）",
        fault.fault_id,
        best.n_cells,
        best.meta["fault_cut"]["watertight"],
    )
    return best


__all__ = ["fault_plane", "cut_mesh_by_fault"]
