"""统计分析服务（M7）：厚度/品位/断层/质量/体积统计与图表、报表导出。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List

from geomodel3d.algorithms.statistics import (
    fault_statistics,
    grade_statistics,
    mesh_layer_volumes,
    quality_statistics,
    thickness_statistics,
)
from geomodel3d.app.events import STATS_READY, EventBus
from geomodel3d.config.errors import ErrorCode, GeoModelError
from geomodel3d.dataio import charts
from geomodel3d.services.data_service import DataService
from geomodel3d.services.project_service import ProjectService

logger = logging.getLogger(__name__)


class StatsService:
    """统计用例服务。"""

    def __init__(self, bus: EventBus, projects: ProjectService, data: DataService) -> None:
        self._bus = bus
        self._projects = projects
        self._data = data

    # ------------------------------------------------------------------ #
    def collect(self, *, bins: int = 20) -> Dict[str, object]:
        """一次性汇总全部统计指标。"""
        project = self._projects._require_project()
        layer_codes = [entry.layer_code for entry in project.config.stratigraphy]
        payload: Dict[str, object] = {
            "thickness": thickness_statistics(project.drillholes, layer_codes, bins=bins),
            "grade": grade_statistics(project.drillholes, layer_codes, bins=bins),
            "faults": fault_statistics(project.faults),
            "quality": quality_statistics(self._quality_report()),
        }
        if project.has_model():
            payload["layer_volumes"] = mesh_layer_volumes(project.mesh, layer_codes)
        else:
            payload["layer_volumes"] = {}
        self._bus.publish(STATS_READY, payload, source="stats")
        return payload

    def thickness(self, *, bins: int = 20) -> Dict:
        project = self._projects._require_project()
        layer_codes = [entry.layer_code for entry in project.config.stratigraphy]
        return thickness_statistics(project.drillholes, layer_codes, bins=bins)

    def grade(self, *, bins: int = 20) -> Dict:
        project = self._projects._require_project()
        layer_codes = [entry.layer_code for entry in project.config.stratigraphy]
        return grade_statistics(project.drillholes, layer_codes, bins=bins)

    def faults(self) -> Dict:
        project = self._projects._require_project()
        return fault_statistics(project.faults)

    def quality(self) -> Dict:
        return quality_statistics(self._quality_report())

    # ------------------------------------------------------------------ #
    def export_charts(self, out_dir: Path, *, fmt: str = "png") -> List[Path]:
        """导出全部统计图表。"""
        payload = self.collect()
        out_dir = Path(out_dir)
        outputs: List[Path] = []
        outputs.append(charts.thickness_chart(payload["thickness"], out_dir / f"thickness.{fmt}"))
        outputs.append(charts.grade_chart(payload["grade"], out_dir / f"grade.{fmt}"))
        outputs.append(charts.fault_chart(payload["faults"], out_dir / f"faults.{fmt}"))
        outputs.append(charts.quality_chart(payload["quality"], out_dir / f"quality.{fmt}"))
        volumes = payload.get("layer_volumes") or {}
        if volumes:
            outputs.append(charts.layer_volume_chart(volumes, out_dir / f"layer_volume.{fmt}"))
        logger.info("统计图表已导出 %d 张到 %s", len(outputs), out_dir)
        return outputs

    def export_excel(self, path: Path) -> Path:
        """导出统计报表（多工作表）。"""
        try:
            import pandas as pd
        except Exception as exc:  # noqa: BLE001
            raise GeoModelError(ErrorCode.EXP_CHK_001, "导出统计报表需要 pandas/openpyxl。") from exc

        payload = self.collect()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            _thickness_frame(pd, payload["thickness"]).to_excel(
                writer, sheet_name="厚度统计", index=False
            )
            pd.DataFrame([payload["grade"]["summary"]]).to_excel(
                writer, sheet_name="品位统计", index=False
            )
            pd.DataFrame([payload["faults"]["dip"], payload["faults"]["throw"]]).to_excel(
                writer, sheet_name="断层统计", index=False
            )
            pd.DataFrame(
                [{"级别": k, "数量": v} for k, v in payload["quality"]["by_code"].items()]
            ).to_excel(writer, sheet_name="数据质量", index=False)
            volumes = payload.get("layer_volumes") or {}
            if volumes:
                pd.DataFrame(
                    [{"地层": k, "体积(m³)": v} for k, v in volumes.items()]
                ).to_excel(writer, sheet_name="地层体积", index=False)
        logger.info("统计报表已导出: %s", path)
        return path

    # ------------------------------------------------------------------ #
    def _quality_report(self):
        return self._data.get_quality_report()


def _thickness_frame(pd, thickness: Dict[str, Dict]):
    rows = []
    for code, entry in thickness.items():
        summary = entry["summary"]
        rows.append({"地层": code, **{k: summary[k] for k in ("count", "mean", "std", "min", "max")}})
    return pd.DataFrame(rows)


__all__ = ["StatsService"]
