"""成果导出服务（M8）：导出前检查单 + 多格式导出（FR-24 / BR-09）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from geomodel3d.algorithms.mesh_health import HealthReport, health_check
from geomodel3d.app.events import EventBus
from geomodel3d.config.errors import ErrorCode, GeoModelError
from geomodel3d.dataio.crs import validate_crs
from geomodel3d.dataio.exporters import export_mesh as _export_mesh
from geomodel3d.dataio.exporters import export_section as _export_section
from geomodel3d.domain.section import SectionResult
from geomodel3d.services.project_service import ProjectService

logger = logging.getLogger(__name__)

VECTOR_FORMATS = {"shp", "geojson", "json", "dxf"}


@dataclass
class ChecklistItem:
    name: str
    ok: bool
    detail: str = ""
    blocking: bool = True


@dataclass
class ExportChecklist:
    items: List[ChecklistItem] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(item.ok or not item.blocking for item in self.items)

    def add(self, name: str, ok: bool, detail: str = "", blocking: bool = True) -> None:
        self.items.append(ChecklistItem(name, ok, detail, blocking))


class ExportService:
    """统一导出入口，先检查单后导出。"""

    def __init__(self, bus: EventBus, projects: ProjectService) -> None:
        self._bus = bus
        self._projects = projects

    # ------------------------------------------------------------------ #
    def checklist(self, *, fmt: str = "vtu", vector: Optional[bool] = None) -> ExportChecklist:
        project = self._projects._require_project()
        vector = vector if vector is not None else fmt.lower() in VECTOR_FORMATS
        checklist = ExportChecklist()

        checklist.add("已生成统一模型", project.has_model(), "缺少模型，无法导出。")
        if project.has_model():
            report = health_check(project.mesh)
            checklist.add(
                "网格拓扑体检",
                report.passed,
                _health_detail(report),
                blocking=True,
            )
        if vector:
            ok = validate_crs(project.crs)
            checklist.add("坐标系已配置", ok, "缺少有效 CRS，禁止导出带大地坐标的矢量成果。")
        checklist.add(
            "工程指标快照",
            True,
            f"插值={project.config.interp.method}，储量体重={project.config.reserve.density}",
            blocking=False,
        )
        return checklist

    def health_report(self) -> HealthReport:
        project = self._projects._require_project()
        if not project.has_model():
            raise GeoModelError(ErrorCode.RSV_GEO_001, "尚无统一模型。")
        return health_check(project.mesh)

    def export_mesh(self, path: Path, fmt: Optional[str] = None, *, force: bool = False) -> Path:
        project = self._projects._require_project()
        if not project.has_model():
            raise GeoModelError(ErrorCode.EXP_CHK_001, "尚无统一模型，无法导出。")
        checklist = self.checklist(fmt=fmt or Path(path).suffix.lstrip("."))
        if not checklist.passed and not force:
            raise GeoModelError(
                ErrorCode.EXP_CHK_001,
                "导出前检查单未通过。",
                context={"items": [item.__dict__ for item in checklist.items if not item.ok]},
            )
        return _export_mesh(project.mesh, Path(path), fmt)

    def export_section(
        self, result: SectionResult, path: Path, fmt: Optional[str] = None, *, force: bool = False
    ) -> Path:
        checklist = self.checklist(fmt=fmt or Path(path).suffix.lstrip("."))
        if not checklist.passed and not force:
            raise GeoModelError(
                ErrorCode.EXP_CHK_001,
                "导出前检查单未通过。",
                context={"items": [item.__dict__ for item in checklist.items if not item.ok]},
            )
        return _export_section(result, Path(path), fmt)


def _health_detail(report: HealthReport) -> str:
    summary = report.summary()
    return (
        f"错误 {summary['error']} · 告警 {summary['warning']} · "
        f"单元 {int(report.metrics.get('cells', 0))}"
    )


__all__ = ["ExportService", "ChecklistItem", "ExportChecklist"]
