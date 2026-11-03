"""headless 批处理运行器（M10-F4 / FR-27）。

用法::

    geomodel3d-cli run --job job.json --out out_dir

Job 描述文件（JSON）字段：

- ``name``            工程名
- ``bounds``          ``{"min": [x,y,z], "max": [x,y,z]}``
- ``crs_epsg``        目标 CRS（可选）
- ``stratigraphy``    ``[{"layer_code": "A", "order": 0, "color": "#..."}]``
- ``boreholes_csv``   钻孔文件路径（相对 job 文件所在目录）
- ``faults_json``     断层 JSON 路径（可选）
- ``resolution``      ``[nx, ny, nz]``
- ``engine``          ``auto`` / ``gempy`` / ``opengeode`` / ``mock``
- ``export``          导出格式列表，如 ``["vtu"]``
- ``reserve``         储量指标（可选）
- ``sections``        剖面批量方案（可选）
- ``repair``          自动修复网格（bool），或 RepairOptions 字段字典
                       （如 ``{"fill_holes": true, "fix_layer_order": true}``）
- ``statistics``      统计图表/报表导出开关
- ``iterate``         参数迭代矩阵（分辨率/插值等）
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from geomodel3d.algorithms.repair import RepairOptions
from geomodel3d.config.schema import AppSettings, Bounds
from geomodel3d.domain.reserve import ReserveMetrics
from geomodel3d.services.modeling_service import spec_from_project

logger = logging.getLogger(__name__)


def _safe_name(name: str) -> str:
    """把工程名清洗为安全的文件名（防路径穿越）。"""
    cleaned = "".join(ch for ch in str(name) if ch.isalnum() or ch in "-_")
    return cleaned or "untitled"


def _repair_options(value):
    """把 Job 中的 repair 字段转为 RepairOptions；bool 表示使用默认选项。"""
    if isinstance(value, dict):
        return RepairOptions(**value)
    return None


def run_job(job_path: Path, out_dir: Path) -> Dict[str, Any]:
    """执行 Job 描述文件，返回结果摘要。"""
    from geomodel3d.app.context import AppContext

    job_path = Path(job_path)
    job = json.loads(job_path.read_text(encoding="utf-8"))
    base = job_path.parent
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    context = AppContext(AppSettings(theme="light"), log_dir=out_dir / "logs", enable_tasks=False)
    try:
        return _run_job_body(context, job, base, out_dir)
    finally:
        context.shutdown()


def _run_job_body(context, job: Dict[str, Any], base: Path, out_dir: Path) -> Dict[str, Any]:
    from geomodel3d.config.schema import ProjectConfig

    project = context.projects.new_project(
        _safe_name(job.get("name", "job")),
        crs_epsg=job.get("crs_epsg"),
        bounds=Bounds(**job["bounds"]) if job.get("bounds") else None,
    )
    if job.get("stratigraphy"):
        # 通过 model_validate 触发重复 layer_code/order 校验
        config = ProjectConfig.model_validate(
            {
                **project.config.model_dump(mode="json"),
                "stratigraphy": job["stratigraphy"],
            }
        )
        project.config = config

    if job.get("boreholes_csv"):
        context.data.import_boreholes(base / job["boreholes_csv"])
    if job.get("faults_json"):
        context.data.import_faults(base / job["faults_json"])

    spec = spec_from_project(
        project,
        engine=job.get("engine", "auto"),
        purpose=job.get("purpose", "high_precision"),
        resolution=tuple(job.get("resolution", [48, 48, 32])),
    )
    meta = context.modeling.create_run(spec, async_run=False)

    repair_summary: Dict[str, Any] = {}
    if job.get("repair"):
        repair_report = context.repair.repair(_repair_options(job.get("repair")))
        repair_summary = {
            "actions": repair_report.actions,
            "before_cells": repair_report.before_cells,
            "after_cells": repair_report.after_cells,
        }

    project_path = context.projects.save(out_dir / f"{project.name}.gm3d")
    exports: List[str] = []
    for fmt in job.get("export", []):
        path = _export(project, out_dir, fmt)
        if path:
            exports.append(str(path))

    reserve_summary: Dict[str, Any] = {}
    if job.get("reserve"):
        metrics = ReserveMetrics.model_validate(job["reserve"])
        reserve_result = context.reserve.plan(metrics)
        report = context.reserve.export_report(reserve_result, out_dir / "reserve_report.xlsx")
        exports.append(str(report))
        reserve_summary = {
            "tonnage": reserve_result.total_tonnage,
            "metal": reserve_result.total_metal,
            "avg_grade": reserve_result.avg_grade,
            "by_class": {key: value.tonnage for key, value in reserve_result.by_class.items()},
        }

    section_outputs: List[str] = []
    for section_job in job.get("sections", []):
        section_outputs.extend(_run_sections(context, out_dir, section_job))

    stats_outputs: List[str] = []
    if job.get("statistics"):
        stats_cfg = job["statistics"]
        if not isinstance(stats_cfg, dict):
            stats_cfg = {}
        if stats_cfg.get("charts", True):
            stats_outputs.extend(str(p) for p in context.stats.export_charts(out_dir / "charts"))
        if stats_cfg.get("excel", True):
            stats_outputs.append(str(context.stats.export_excel(out_dir / "statistics.xlsx")))

    result = {
        "project": str(project_path),
        "run_id": meta.run_id,
        "status": meta.status.value,
        "cells": project.mesh.n_cells if project.mesh else 0,
        "volume": project.mesh.total_volume() if project.mesh else 0.0,
        "exports": exports + section_outputs + stats_outputs,
        "reserve": reserve_summary,
        "repair": repair_summary,
    }

    iterations = _run_iterations(context, project, job, out_dir)
    if iterations:
        result["iterations"] = iterations

    (out_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def _run_iterations(context, project, job: Dict[str, Any], out_dir: Path) -> List[Dict[str, Any]]:
    """参数迭代建模（M10-F2）：对参数矩阵逐个建模并记录指标。"""
    overrides = job.get("iterate", [])
    if not overrides:
        return []
    results: List[Dict[str, Any]] = []
    for index, override in enumerate(overrides):
        resolution = tuple(override.get("resolution", job.get("resolution", [48, 48, 32])))
        spec = spec_from_project(
            project,
            engine=override.get("engine", job.get("engine", "auto")),
            purpose=job.get("purpose", "high_precision"),
            resolution=resolution,
        )
        if override.get("interp"):
            from geomodel3d.config.schema import InterpConfig

            spec.interp = InterpConfig(**override["interp"])
        meta = context.modeling.create_run(spec, async_run=False)
        results.append(
            {
                "index": index + 1,
                "params": {"resolution": list(resolution), **override},
                "cells": project.mesh.n_cells if project.mesh else 0,
                "volume": project.mesh.total_volume() if project.mesh else 0.0,
                "duration_s": meta.duration_s,
            }
        )
    (out_dir / "iterate_summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return results


def _run_sections(context, out_dir: Path, spec: Dict[str, Any]) -> List[str]:
    """批量生成剖面并导出（单个剖面失败不影响其余）。"""
    outputs: List[str] = []
    fmt = spec.get("format", "geojson")
    for index, elevation in enumerate(spec.get("horizontal", [])):
        try:
            result = context.section.create_horizontal(float(elevation))
            path = out_dir / f"section_h{elevation:g}_{index + 1:02d}.{fmt}"
            context.section.export(result, path, fmt)
            outputs.append(str(path))
        except Exception:  # noqa: BLE001
            logger.exception("水平切面 %s 导出失败", elevation)
    for index, line in enumerate(spec.get("lines", [])):
        try:
            if len(line) != 4:
                raise ValueError("剖面线需要 [x1, y1, x2, y2] 四个坐标")
            x1, y1, x2, y2 = (float(v) for v in line)
            result = context.section.create_arbitrary([x1, y1], [x2, y2])
            path = out_dir / f"section_line{index + 1:02d}.{fmt}"
            context.section.export(result, path, fmt)
            outputs.append(str(path))
        except Exception:  # noqa: BLE001
            logger.exception("剖面线 %d 导出失败", index + 1)
    return outputs


def _export(project, out_dir: Path, fmt: str) -> Optional[Path]:
    if project.mesh is None:
        return None
    from geomodel3d.dataio.exporters import export_mesh

    fmt = fmt.lower()
    if fmt == "og_hso3d":
        path = out_dir / f"{project.name}.og_hso3d"
    else:
        path = out_dir / f"{project.name}.{fmt}"
    try:
        return export_mesh(project.mesh, path, fmt)
    except Exception:  # noqa: BLE001
        logger.exception("导出 %s 失败", fmt)
        return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="geomodel3d-cli", description="三维地质建模 headless 批处理")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="执行 Job 描述文件")
    run_parser.add_argument("--job", required=True, type=Path, help="Job JSON 文件")
    run_parser.add_argument("--out", required=True, type=Path, help="输出目录")
    args = parser.parse_args(argv)

    if args.command == "run":
        result = run_job(args.job, args.out)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
