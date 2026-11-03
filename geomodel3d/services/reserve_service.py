"""储量计算服务（M4）：方案、计算、灵敏度、报告导出。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from geomodel3d.algorithms.reserve import estimate_reserves
from geomodel3d.app.events import RESERVE_READY, EventBus
from geomodel3d.config.errors import ErrorCode, GeoModelError
from geomodel3d.domain.reserve import ReserveMetrics, ReserveResult
from geomodel3d.services.project_service import ProjectService

logger = logging.getLogger(__name__)


class ReserveService:
    """储量用例服务。"""

    def __init__(self, bus: EventBus, projects: ProjectService) -> None:
        self._bus = bus
        self._projects = projects

    # ------------------------------------------------------------------ #
    def plan(
        self,
        metrics: ReserveMetrics,
        *,
        body_id: str = "ore_body",
    ) -> ReserveResult:
        """按指标对当前统一模型执行储量估算。"""
        project = self._projects._require_project()
        if not project.has_model():
            raise GeoModelError(ErrorCode.RSV_GEO_001, "尚无统一模型，无法计算储量。")
        result = estimate_reserves(
            project.mesh,
            metrics,
            drillholes=project.drillholes,
            interp=project.config.interp,
            body_id=body_id,
        )
        logger.info("储量计算完成: %s", result.summary_text())
        self._bus.publish(RESERVE_READY, result, source="reserve")
        return result

    def sensitivity(
        self,
        metrics: ReserveMetrics,
        *,
        field: str = "cutoff_grade",
        deltas: Optional[Sequence[float]] = None,
        body_id: str = "ore_body",
    ) -> List[Dict[str, float]]:
        """指标灵敏度试算：逐个取值重算矿量。"""
        if field not in {"cutoff_grade", "min_thickness", "density"}:
            raise GeoModelError(ErrorCode.RSV_IND_001, f"不支持灵敏度的字段: {field}")
        project = self._projects._require_project()
        if not project.has_model():
            raise GeoModelError(ErrorCode.RSV_GEO_001, "尚无统一模型，无法试算。")
        base = getattr(metrics, field)
        if deltas is None:
            deltas = [-0.4, -0.2, 0.0, 0.2, 0.4]
        rows: List[Dict[str, float]] = []
        for delta in deltas:
            value = max(0.0, base * (1.0 + delta))
            variant = metrics.model_copy(update={field: value})
            result = estimate_reserves(
                project.mesh,
                variant,
                drillholes=project.drillholes,
                interp=project.config.interp,
                body_id=body_id,
            )
            rows.append(
                {
                    "value": value,
                    "volume": result.total_volume,
                    "tonnage": result.total_tonnage,
                    "metal": result.total_metal,
                    "avg_grade": result.avg_grade,
                }
            )
        return rows

    def export_report(self, result: ReserveResult, path: Path) -> Path:
        """导出 XLSX 储量报告（8.6 工作表结构）。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import pandas as pd
        except Exception as exc:  # noqa: BLE001
            raise GeoModelError(ErrorCode.EXP_CHK_001, "导出报告需要 pandas/openpyxl。") from exc

        metrics = result.metrics
        cover = pd.DataFrame(
            {
                "项目": [result.body_id],
                "矿种": [metrics.mineral],
                "计算方法": ["体积法" if result.method == "volume" else "块段法"],
                "生成时间": [result.created],
                "总矿石量(t)": [round(result.total_tonnage, 2)],
                "总金属量": [round(result.total_metal, 2)],
                "平均品位": [round(result.avg_grade, 4)],
                "平均厚度(m)": [round(result.avg_thickness, 3)],
            }
        )
        params = pd.DataFrame(
            [{"指标": k, "取值": v} for k, v in metrics.model_dump().items() if k != "block_size"]
        )
        summary = pd.DataFrame(
            [
                {
                    "分级": key,
                    "块段数": value.block_count,
                    "体积(m³)": round(value.volume, 2),
                    "矿石量(t)": round(value.tonnage, 2),
                    "金属量": round(value.metal, 2),
                    "平均品位": round(value.avg_grade, 4),
                    "平均厚度(m)": round(value.avg_thickness, 3),
                }
                for key, value in result.by_class.items()
            ]
        )
        blocks = pd.DataFrame([block.model_dump() for block in result.blocks])
        levels = pd.DataFrame(result.by_level)

        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            cover.to_excel(writer, sheet_name="封面", index=False)
            params.to_excel(writer, sheet_name="参数表", index=False)
            summary.to_excel(writer, sheet_name="分级汇总", index=False)
            blocks.to_excel(writer, sheet_name="块段明细", index=False)
            levels.to_excel(writer, sheet_name="中段汇总", index=False)
        logger.info("储量报告已导出: %s", path)
        return path


__all__ = ["ReserveService"]
