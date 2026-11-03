"""GemPy 引擎适配器（快速建模 / 概率插值）。

GemPy 为可选依赖（LGPL-3.0）。支持两种接入方式：

1. **进程内**：当前解释器可 ``import gempy``（3.x 新引擎，需 Python ≥3.10）；
2. **外部桥接**：通过环境变量 ``GEOMODEL3D_GEMPY_PYTHON`` 指定安装了
   GemPy 3.x 的解释器（或自动发现 conda 环境 ``geomodel3d-gempy3``），
   以子进程执行 :mod:`geomodel3d.engine._gempy_bridge` 完成真实插值，
   再把 ``lith_block`` 转成统一模型 UMesh。

GemPy ``lith_block`` 形状为 ``(nx, ny, nz)``（C 序），与本项目体元顺序一致，
1 基地层编号减 1 后即映射到地层字典顺序。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from geomodel3d.config.errors import EngineUnavailableError, GeoModelError, ErrorCode
from geomodel3d.domain.entities import Drillhole
from geomodel3d.engine.base import EngineCapabilities, ModelingEngine, ModelingSpec
from geomodel3d.engine.layered import (
    _axes,
    _layer_boundaries,
    _map_codes,
    _mid,
    _surface_meta,
    _voxel_grid,
)
from geomodel3d.engine.umesh import VTK_HEXAHEDRON, UMesh

logger = logging.getLogger(__name__)

BRIDGE_SCRIPT = Path(__file__).resolve().parent / "_gempy_bridge.py"
_BRIDGE_PROBE_CACHE: Dict[str, bool] = {}
_RESOLVED_PYTHON: Dict[str, Optional[str]] = {"value": None}
_RESOLVE_LOCK = threading.Lock()
_RESOLVE_DONE = False


# --------------------------------------------------------------------------- #
# 可用性检测
# --------------------------------------------------------------------------- #


def _load_gempy():
    """返回进程内可用的 gempy 模块（3.x 优先），否则 ``None``。"""
    try:
        import gempy  # type: ignore

        if hasattr(gempy, "create_geomodel"):
            return gempy
    except Exception:  # noqa: BLE001
        pass
    return None


def _candidate_pythons() -> List[str]:
    """按优先级返回可能安装了 GemPy 的解释器候选。"""
    explicit = os.environ.get("GEOMODEL3D_GEMPY_PYTHON")
    if explicit and Path(explicit).exists():
        return [explicit]
    candidates: List[Path] = []
    prefixes = {Path(sys.prefix), Path(getattr(sys, "base_prefix", sys.prefix))}
    for prefix in prefixes:
        for envs in (prefix / "envs", prefix.parent / "envs"):
            if not envs.exists():
                continue
            candidates.extend(sorted(envs.glob("geomodel3d-gempy3*/python.exe")))
            candidates.extend(sorted(envs.glob("geomodel3d-gempy*/python.exe")))
            candidates.extend(sorted(envs.glob("*/python.exe")))
    seen: List[str] = []
    for candidate in candidates:
        if candidate.exists() and str(candidate) not in seen:
            seen.append(str(candidate))
    return seen


def _find_gempy_python() -> Optional[str]:
    """返回首个可导入 GemPy 的外部解释器（线程安全 + 正/负缓存）。"""
    global _RESOLVE_DONE
    with _RESOLVE_LOCK:
        if _RESOLVED_PYTHON["value"] is not None:
            return _RESOLVED_PYTHON["value"]
        if _RESOLVE_DONE:
            return None
        for candidate in _candidate_pythons():
            if _external_gempy_works(candidate):
                _RESOLVED_PYTHON["value"] = candidate
                _RESOLVE_DONE = True
                return candidate
        _RESOLVE_DONE = True
        return None


def _external_gempy_works(python: Optional[str]) -> bool:
    if not python:
        return False
    if python in _BRIDGE_PROBE_CACHE:
        return _BRIDGE_PROBE_CACHE[python]
    try:
        completed = subprocess.run(
            [python, "-c", "import gempy; assert hasattr(gempy, 'create_geomodel')"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        ok = completed.returncode == 0 and "Traceback" not in completed.stderr
    except Exception:  # noqa: BLE001
        ok = False
    _BRIDGE_PROBE_CACHE[python] = ok
    return ok


def gempy_available() -> bool:
    if _load_gempy() is not None:
        return True
    return _find_gempy_python() is not None


class GemPyEngine(ModelingEngine):
    name = "gempy"
    capabilities = EngineCapabilities(
        name="gempy",
        high_precision=False,
        fault_cut=False,
        closed_solids=False,
        smooth_surfaces=True,
        available=False,
    )

    def is_available(self) -> bool:
        return gempy_available()

    def build(self, spec: ModelingSpec, ctx) -> UMesh:
        gempy = _load_gempy()
        try:
            if gempy is not None:
                ctx.progress(5, "GemPy 进程内计算")
                lith = _compute_lith_inprocess(gempy, spec, ctx)
            else:
                python = _find_gempy_python()
                if not _external_gempy_works(python):
                    raise EngineUnavailableError(
                        "GemPy 未安装或不可用（可设置 GEOMODEL3D_GEMPY_PYTHON 指向含 GemPy 3.x 的解释器）。",
                        context={"engine": self.name},
                    )
                ctx.progress(5, "GemPy 外部桥接计算")
                lith = _compute_lith_bridge(python, spec, ctx)
        except EngineUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EngineUnavailableError(
                f"GemPy 建模失败：{exc}",
                context={"engine": self.name},
            ) from exc
        ctx.progress(85, "转换 GemPy 结果")
        return _lith_to_mesh(lith, spec)


# --------------------------------------------------------------------------- #
# 约束构造
# --------------------------------------------------------------------------- #


def _build_constraints(layers: Sequence[Any], drillholes: Sequence[Drillhole]):
    """构造界面点（第 i 个界面取下一层顶面）与水平产状。"""
    import pandas as pd

    surface_rows: List[Dict[str, float]] = []
    orientation_rows: List[Dict[str, float]] = []
    for index in range(len(layers) - 1):
        upper_code = layers[index].layer_code
        lower_code = layers[index + 1].layer_code
        points: List[List[float]] = []
        for hole in drillholes:
            for interval in hole.intervals:
                if interval.layer_code == lower_code:
                    points.append([hole.x, hole.y, hole.collar_elev - interval.depth_top])
                    break
        for x, y, z in points:
            surface_rows.append({"X": x, "Y": y, "Z": z, "surface": upper_code})
        if points:
            arr = np.asarray(points, dtype=float)
            orientation_rows.append(
                {
                    "X": float(arr[:, 0].mean()),
                    "Y": float(arr[:, 1].mean()),
                    "Z": float(arr[:, 2].mean()),
                    "surface": upper_code,
                    "dip": 0.0,
                    "azimuth": 0.0,
                    "polarity": 1.0,
                }
            )
    return pd.DataFrame(surface_rows), pd.DataFrame(orientation_rows)


def _plane_pole(points: np.ndarray) -> np.ndarray:
    if points.shape[0] < 3:
        return np.array([0.0, 0.0, 1.0])
    a = np.column_stack([points[:, 0], points[:, 1], np.ones(points.shape[0])])
    try:
        coeffs, *_ = np.linalg.lstsq(a, points[:, 2], rcond=None)
    except np.linalg.LinAlgError:
        return np.array([0.0, 0.0, 1.0])
    normal = np.array([-coeffs[0], -coeffs[1], 1.0])
    norm = np.linalg.norm(normal)
    return normal / norm if norm > 0 else np.array([0.0, 0.0, 1.0])


def _interface_samples(
    layers: Sequence[Any], drillholes: Sequence[Drillhole]
) -> List[Dict[str, Any]]:
    samples: List[Dict[str, Any]] = []
    for index in range(len(layers) - 1):
        upper_code = layers[index].layer_code
        lower_code = layers[index + 1].layer_code
        points: List[List[float]] = []
        for hole in drillholes:
            for interval in hole.intervals:
                if interval.layer_code == lower_code:
                    points.append([hole.x, hole.y, hole.collar_elev - interval.depth_top])
                    break
        if points:
            samples.append({"upper": upper_code, "points": np.asarray(points, dtype=float)})
    return samples


# --------------------------------------------------------------------------- #
# 进程内 GemPy 3.x
# --------------------------------------------------------------------------- #


def _compute_lith_inprocess(gempy, spec: ModelingSpec, ctx) -> np.ndarray:
    layers = sorted(spec.stratigraphy, key=lambda entry: entry.order)
    samples = _interface_samples(layers, spec.drillholes)
    if not samples:
        raise GeoModelError(ErrorCode.MDL_IPL_002, "缺少可用于 GemPy 的界面点。")

    nx, ny, nz = (int(v) for v in spec.resolution)
    geo_model = gempy.create_geomodel(
        project_name="geomodel3d",
        extent=[spec.bounds.min[0], spec.bounds.max[0],
                spec.bounds.min[1], spec.bounds.max[1],
                spec.bounds.min[2], spec.bounds.max[2]],
        resolution=[nx, ny, nz],
        refinement=1,
        structural_frame=gempy.data.StructuralFrame.initialize_default_structure(),
    )
    for index, sample in enumerate(samples):
        points = sample["points"]
        pole = _plane_pole(points)
        name = f"surface{index + 1}"
        if index == 0:
            gempy.add_surface_points(
                geo_model=geo_model, x=points[:, 0], y=points[:, 1], z=points[:, 2],
                elements_names=name,
            )
            gempy.add_orientations(
                geo_model=geo_model, x=[float(points[:, 0].mean())],
                y=[float(points[:, 1].mean())], z=[float(points[:, 2].mean())],
                elements_names=[name], pole_vector=[pole.tolist()],
            )
        else:
            element = gempy.data.StructuralElement(
                name=name,
                color=next(geo_model.structural_frame.color_generator),
                surface_points=gempy.data.SurfacePointsTable.from_arrays(
                    x=points[:, 0], y=points[:, 1], z=points[:, 2], names=name
                ),
                orientations=gempy.data.OrientationsTable.from_arrays(
                    x=np.array([float(points[:, 0].mean())]),
                    y=np.array([float(points[:, 1].mean())]),
                    z=np.array([float(points[:, 2].mean())]),
                    G_x=np.array([pole[0]]), G_y=np.array([pole[1]]), G_z=np.array([pole[2]]),
                    names=name,
                ),
            )
            geo_model.structural_frame.structural_groups[0].append_element(element)

    ctx.progress(40, "GemPy 插值计算")
    geo_model.update_transform(gempy.data.GlobalAnisotropy.NONE)
    gempy.compute_model(geo_model)
    return np.asarray(geo_model.solutions.raw_arrays.lith_block, dtype=np.int64)


# --------------------------------------------------------------------------- #
# 外部桥接
# --------------------------------------------------------------------------- #


def _compute_lith_bridge(python: str, spec: ModelingSpec, ctx) -> np.ndarray:
    layers = sorted(spec.stratigraphy, key=lambda entry: entry.order)
    job = {
        "name": "geomodel3d",
        "bounds": {"min": list(spec.bounds.min), "max": list(spec.bounds.max)},
        "resolution": [int(v) for v in spec.resolution],
        "layers": [{"layer_code": entry.layer_code, "order": entry.order} for entry in layers],
        "drillholes": [hole.model_dump(mode="json") for hole in spec.drillholes],
    }
    with tempfile.TemporaryDirectory() as tmp:
        # 把桥接脚本复制到临时目录执行，避免脚本所在目录被加入 sys.path 造成模块遮蔽
        # （不使用 -P：该选项仅 Python 3.11+ 支持，而 GemPy 3.x 也支持 3.10）
        bridge_copy = Path(tmp) / "_gempy_bridge.py"
        bridge_copy.write_text(BRIDGE_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
        job_path = Path(tmp) / "job.json"
        out_path = Path(tmp) / "lith.npz"
        job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        ctx.progress(30, "GemPy 子进程计算")
        completed = subprocess.run(
            [python, str(bridge_copy), "--job", str(job_path), "--out", str(out_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
        )
        if completed.returncode != 0 or not out_path.exists():
            raise EngineUnavailableError(
                "GemPy 桥接计算失败：" + (completed.stderr or completed.stdout or "")[-500:],
                context={"engine": "gempy", "python": python},
            )
        with np.load(out_path) as data:
            return np.asarray(data["lith_block"], dtype=np.int64)


# --------------------------------------------------------------------------- #
# 结果转换
# --------------------------------------------------------------------------- #


def _lith_block_to_layer_index(lith_block: np.ndarray, resolution: Sequence[int]) -> np.ndarray:
    """把 GemPy ``lith_block``（1 基地层编号）转换为 0 基单元层号。"""
    nx, ny, nz = (int(v) for v in resolution)
    block = np.asarray(lith_block).reshape(nx, ny, nz)
    return (block.astype(np.int64) - 1).ravel()


def _lith_to_mesh(lith_block: np.ndarray, spec: ModelingSpec) -> UMesh:
    """用 GemPy 的 lith 结果装配 UMesh（复用统一体元网格）。"""
    layers = sorted(spec.stratigraphy, key=lambda entry: entry.order)
    nx, ny, nz = (int(v) for v in spec.resolution)
    xs, ys, zs = _axes(spec.bounds, nx, ny, nz)
    vertices, cells = _voxel_grid(xs, ys, zs)
    layer_index = _lith_block_to_layer_index(lith_block, spec.resolution)
    order_to_code = {index: layer.layer_code for index, layer in enumerate(layers)}
    n_layers = max(len(layers), 1)
    layer_index = np.clip(layer_index, 0, n_layers - 1)
    codes = _map_codes(layer_index, order_to_code)
    face_groups = {
        code: np.nonzero(codes == code)[0].tolist() for code in order_to_code.values()
    }
    # 与 Mock/OpenGeode 保持一致：提供每层顶/底界面，供视口渲染光滑层实体
    layer_surfaces = None
    try:
        x_mid, y_mid = _mid(xs), _mid(ys)
        columns = np.stack(np.meshgrid(x_mid, y_mid, indexing="ij"), axis=-1).reshape(-1, 2)
        boundaries_all = _layer_boundaries(spec, layers, columns)
        layer_surfaces = _surface_meta(boundaries_all, x_mid, y_mid, nx, ny, order_to_code)
    except Exception:  # noqa: BLE001 - 显示数据缺失时回退等值面渲染
        logger.warning("GemPy 结果缺少层界面数据，回退等值面显示", exc_info=True)
    return UMesh(
        vertices=vertices,
        cells=cells,
        cell_types=np.full(len(cells), VTK_HEXAHEDRON, dtype=np.int32),
        cell_attr={"layer": layer_index.astype(np.int32)},
        face_groups=face_groups,
        meta={
            "engine": "gempy",
            "resolution": [nx, ny, nz],
            "layer_codes": list(order_to_code.values()),
            "n_drillholes": len(spec.drillholes),
            "layer_surfaces": layer_surfaces,
        },
    )


__all__ = ["GemPyEngine", "gempy_available"]
