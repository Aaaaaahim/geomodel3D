"""剖面生成服务（M5）：任意线、平行勘探线、水平切面与成果导出。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Sequence

from geomodel3d.algorithms.section import (
    build_section,
    make_arbitrary_section,
    make_exploration_sections,
    make_horizontal_section,
)
from geomodel3d.app.events import SECTION_READY, EventBus
from geomodel3d.config.errors import ErrorCode, GeoModelError
from geomodel3d.dataio.exporters import export_section
from geomodel3d.domain.section import SectionPlan, SectionResult
from geomodel3d.services.project_service import ProjectService

logger = logging.getLogger(__name__)


class SectionService:
    """剖面用例服务。"""

    def __init__(self, bus: EventBus, projects: ProjectService) -> None:
        self._bus = bus
        self._projects = projects

    # ------------------------------------------------------------------ #
    def create(self, plan: SectionPlan, *, ore_layers: Optional[Sequence[str]] = None) -> SectionResult:
        """按方案生成剖面。"""
        project = self._projects._require_project()
        if not project.has_model():
            raise GeoModelError(ErrorCode.SEC_CRS_001, "尚无统一模型，无法生成剖面。")
        if plan.crs_epsg is None:
            plan.crs_epsg = project.crs.epsg
        result = build_section(project.mesh, plan, drillholes=project.drillholes, faults=project.faults)
        result.ore_layers = list(ore_layers if ore_layers is not None else project.config.reserve.body_layers)
        logger.info("剖面生成完成: %s (%d 图层)", plan.name, len(result.polylines))
        self._bus.publish(SECTION_READY, result, source="section")
        return result

    def create_arbitrary(
        self,
        p1: Sequence[float],
        p2: Sequence[float],
        *,
        name: str = "任意剖面",
        offset_window: float = 50.0,
    ) -> SectionResult:
        project = self._projects._require_project()
        plan = make_arbitrary_section(
            p1,
            p2,
            name=name,
            bounds=project.bounds_list(),
            crs_epsg=project.crs.epsg,
            offset_window=offset_window,
        )
        return self.create(plan)

    def create_horizontal(self, elevation: float, *, name: str = "水平切面") -> SectionResult:
        project = self._projects._require_project()
        plan = make_horizontal_section(
            elevation, name=name, bounds=project.bounds_list(), crs_epsg=project.crs.epsg
        )
        return self.create(plan)

    def batch(
        self,
        base_plan: SectionPlan,
        *,
        spacing: float = 200.0,
        count: int = 5,
    ) -> List[SectionResult]:
        """批量生成平行勘探线剖面。"""
        plans = make_exploration_sections(base_plan, spacing=spacing, count=count)
        return [self.create(plan) for plan in plans]

    def export(self, result: SectionResult, path: Path, fmt: Optional[str] = None) -> Path:
        """导出剖面成果；矢量格式缺少 CRS 时阻断（BR-06）。"""
        fmt = (fmt or Path(path).suffix.lstrip(".")).lower()
        if fmt in ("shp", "geojson", "json", "dxf") and not result.plan.crs_epsg:
            raise GeoModelError(
                ErrorCode.SEC_CRS_001,
                "缺少 CRS，禁止导出带大地坐标的矢量剖面。",
                context={"section": result.plan.name},
            )
        return export_section(result, Path(path), fmt)


__all__ = ["SectionService"]
