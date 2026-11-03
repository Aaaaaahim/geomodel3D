"""GemPy 3.x 独立桥接脚本（在外部解释器中运行）。

主程序（可能为 Python 3.9）通过子进程调用本脚本，在安装了 GemPy 3.x 的
解释器中执行真实插值计算，并把 ``lith_block`` 写为 ``.npz`` 供主程序读取。

用法::

    <gempy-python> _gempy_bridge.py --job job.json --out result.npz

本脚本不依赖 geomodel3d 包，只依赖 gempy / numpy / json，便于隔离环境运行。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

import numpy as np


def _interface_points(drillholes: List[Dict], lower_code: str):
    """收集界面点：下一层（lower）的顶面高程。"""
    xs, ys, zs = [], [], []
    for hole in drillholes:
        for interval in hole["intervals"]:
            if interval["layer_code"] == lower_code:
                xs.append(float(hole["x"]))
                ys.append(float(hole["y"]))
                zs.append(float(hole["collar_elev"]) - float(interval["depth_top"]))
                break
    return np.asarray(xs), np.asarray(ys), np.asarray(zs)


def _plane_pole(xs: np.ndarray, ys: np.ndarray, zs: np.ndarray) -> np.ndarray:
    """由点云最小二乘拟合平面，返回单位法向（倾角方向）。"""
    if len(xs) < 3:
        return np.array([0.0, 0.0, 1.0])
    a = np.column_stack([xs, ys, np.ones(len(xs))])
    try:
        coeffs, *_ = np.linalg.lstsq(a, zs, rcond=None)
    except np.linalg.LinAlgError:
        return np.array([0.0, 0.0, 1.0])
    normal = np.array([-coeffs[0], -coeffs[1], 1.0])
    norm = np.linalg.norm(normal)
    return normal / norm if norm > 0 else np.array([0.0, 0.0, 1.0])


def run(job: Dict, out_path: Path) -> Dict:
    import gempy as gp

    bounds = job["bounds"]
    resolution = [int(v) for v in job["resolution"]]
    layers = sorted(job["layers"], key=lambda item: item["order"])
    drillholes = job["drillholes"]

    geo_model = gp.create_geomodel(
        project_name=job.get("name", "geomodel3d"),
        extent=[bounds["min"][0], bounds["max"][0],
                bounds["min"][1], bounds["max"][1],
                bounds["min"][2], bounds["max"][2]],
        resolution=resolution,
        refinement=1,
        structural_frame=gp.data.StructuralFrame.initialize_default_structure(),
    )

    interface_count = 0
    for index in range(len(layers) - 1):
        lower_code = layers[index + 1]["layer_code"]
        xs, ys, zs = _interface_points(drillholes, lower_code)
        if len(xs) == 0:
            continue
        pole = _plane_pole(xs, ys, zs)
        if interface_count == 0:
            gp.add_surface_points(geo_model=geo_model, x=xs, y=ys, z=zs, elements_names="surface1")
            gp.add_orientations(
                geo_model=geo_model, x=[float(xs.mean())], y=[float(ys.mean())], z=[float(zs.mean())],
                elements_names=["surface1"], pole_vector=[pole.tolist()],
            )
        else:
            element = gp.data.StructuralElement(
                name=f"surface{interface_count + 1}",
                color=next(geo_model.structural_frame.color_generator),
                surface_points=gp.data.SurfacePointsTable.from_arrays(
                    x=xs, y=ys, z=zs, names=f"surface{interface_count + 1}"
                ),
                orientations=gp.data.OrientationsTable.from_arrays(
                    x=np.array([float(xs.mean())]), y=np.array([float(ys.mean())]),
                    z=np.array([float(zs.mean())]),
                    G_x=np.array([pole[0]]), G_y=np.array([pole[1]]), G_z=np.array([pole[2]]),
                    names=f"surface{interface_count + 1}",
                ),
            )
            geo_model.structural_frame.structural_groups[0].append_element(element)
        interface_count += 1

    if interface_count == 0:
        raise RuntimeError("没有可用于 GemPy 的界面点。")

    geo_model.update_transform(gp.data.GlobalAnisotropy.NONE)
    gp.compute_model(geo_model)
    lith = np.asarray(geo_model.solutions.raw_arrays.lith_block, dtype=np.int64)
    np.savez(out_path, lith_block=lith, resolution=np.asarray(resolution, dtype=np.int64))
    return {"interfaces": interface_count, "cells": int(lith.size), "unique": sorted(int(v) for v in np.unique(lith))}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="GemPy 3.x 桥接")
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    job = json.loads(Path(args.job).read_text(encoding="utf-8"))
    result = run(job, Path(args.out))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
