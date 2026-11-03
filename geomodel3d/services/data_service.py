"""数据导入与预处理服务（M1）。

解析、校验、清洗、坐标归一，产出标准化数据源与质量报告；
发布 ``data.ready`` / ``data.rejected`` 事件。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

import numpy as np

from geomodel3d.app.events import (
    DATA_READY,
    DATA_REJECTED,
    PROJECT_CLOSED,
    PROJECT_OPENED,
    EventBus,
)
from geomodel3d.config.errors import ErrorCode, GeoModelError
from geomodel3d.config.schema import CRSConfig, Bounds
from geomodel3d.dataio.borehole_io import read_boreholes
from geomodel3d.dataio.crs import CRSService
from geomodel3d.dataio.seismic_io import read_seismic_multi
from geomodel3d.domain.entities import Fault, FaultParams, FaultSide, SeismicLayer
from geomodel3d.domain.rules import (
    QualityReport,
    Severity,
    derive_stratigraphy,
    validate_boreholes,
)
from geomodel3d.services.project_service import ProjectService

logger = logging.getLogger(__name__)


class DataService:
    """多源数据导入与预处理用例服务。"""

    def __init__(self, bus: EventBus, projects: ProjectService) -> None:
        self._bus = bus
        self._projects = projects
        self._quality = QualityReport()
        self._crs = CRSService()
        bus.subscribe(PROJECT_OPENED, lambda _event: self._reset_quality())
        bus.subscribe(PROJECT_CLOSED, lambda _event: self._reset_quality())

    def _reset_quality(self) -> None:
        self._quality = QualityReport()

    # ------------------------------------------------------------------ #
    def import_boreholes(
        self, path: Path, mapping: Optional[Dict[str, Optional[str]]] = None
    ) -> QualityReport:
        project = self._projects._require_project()
        holes = read_boreholes(Path(path), mapping=mapping)
        stratigraphy = list(project.config.stratigraphy)
        if not stratigraphy:
            stratigraphy = derive_stratigraphy(holes)
            logger.info("自动推导地层字典: %d 层", len(stratigraphy))
        valid_layers = {entry.layer_code for entry in stratigraphy}
        reset_bounds = self._autofit_bounds(project, holes)
        report = validate_boreholes(
            holes,
            valid_layers=valid_layers or None,
            grade_range=(0.0, 100.0),
            bounds=project.bounds_list(),
        )
        if reset_bounds:
            report.add(
                Severity.INFO,
                "DAT-BND-AUTO",
                "工程包围盒与钻孔坐标不匹配，已按钻孔范围自动重设（留边 10%）。",
            )
        self._quality = report
        if not report.passed:
            self._bus.publish(
                DATA_REJECTED,
                {"path": str(path), "report": report},
                source="data",
            )
            raise GeoModelError(
                ErrorCode.DAT_VAL_001,
                f"钻孔数据校验未通过，存在 {len(report.errors)} 个错误。",
                context={"report": report.model_dump()},
            )
        # 校验通过后再同时提交地层字典与钻孔，避免半成品状态
        project.config.stratigraphy = stratigraphy
        project.drillholes = holes
        self._fill_crs_from_data(project, holes)
        project.mark_dirty()
        logger.info("导入钻孔: %s (%d 孔)", path, len(holes))
        self._bus.publish(
            DATA_READY,
            {"kind": "boreholes", "count": len(holes), "report": report},
            source="data",
        )
        return report

    def import_faults(self, path: Path) -> List[Fault]:
        project = self._projects._require_project()
        faults = _read_faults(Path(path))
        project.faults = faults
        project.mark_dirty()
        logger.info("导入断层: %s (%d 条)", path, len(faults))
        self._bus.publish(
            DATA_READY, {"kind": "faults", "count": len(faults)}, source="data"
        )
        return faults

    def import_seismic(self, path: Union[Path, Sequence[Path]]) -> SeismicLayer:
        project = self._projects._require_project()
        layer = read_seismic_multi(path)
        project.seismic.append(layer)
        project.mark_dirty()
        logger.info("导入地震趋势面: %s", path)
        self._bus.publish(
            DATA_READY, {"kind": "seismic", "count": len(project.seismic)}, source="data"
        )
        return layer

    def unify_crs(self, target: Optional[CRSConfig] = None) -> None:
        """把数据统一到工程目标 CRS（记录变换链，8.5）。"""
        project = self._projects._require_project()
        target = target or project.crs
        if not (target.epsg or target.proj4 or target.wkt):
            raise GeoModelError(ErrorCode.DAT_CRS_001, "工程未指定目标 CRS。")
        source = project.config.project.source_crs
        if not source or (not source.epsg and not source.proj4):
            return
        project_same = _same_crs(source, target)

        if not project_same:
            collars = np.array(
                [[h.x, h.y, h.collar_elev] for h in project.drillholes], dtype=np.float64
            )
            if collars.size:
                transformed = self._crs.transform_points(collars, source, target)
                for hole, row in zip(project.drillholes, transformed):
                    hole.x, hole.y = float(row[0]), float(row[1])
            for fault in project.faults:
                if fault.trace:
                    trace = np.asarray(fault.trace, dtype=np.float64)
                    fault.trace = self._crs.transform_points(trace, source, target).tolist()
        # 地震层位按其自带源 CRS 逐源变换（P1-3 / 8.5）
        for layer in project.seismic:
            if not layer.points:
                continue
            layer_source = _seismic_source(layer, source)
            if layer_source is None:
                logger.warning(
                    "地震层 %s 缺少源 CRS，跳过坐标变换；请在导入时指定或设置工程 source_crs。",
                    layer.name or layer.layer_id,
                )
                continue
            if _same_crs(layer_source, target):
                layer.source_crs_epsg = target.epsg
                continue
            points = np.asarray(layer.points, dtype=np.float64)
            layer.points = self._crs.transform_points(points, layer_source, target).tolist()
            layer.source_crs_epsg = target.epsg
        project.config.project.source_crs = target.model_copy(deep=True)
        logger.info("坐标统一: EPSG %s -> %s", source.epsg, target.epsg)
        project.mark_dirty()

    def _fill_crs_from_data(self, project, holes) -> None:
        """工程未设置 CRS 时，按钻孔坐标自动识别并补充（启发式）。"""
        crs = project.crs
        if crs.epsg or crs.proj4 or crs.wkt:
            return
        from geomodel3d.config.schema import CRSConfig
        from geomodel3d.dataio.crs_detect import detect_crs

        suggestion = detect_crs([[hole.x, hole.y] for hole in holes])[0]
        if suggestion.usable:
            project.config.project.crs = CRSConfig(epsg=suggestion.epsg, name=suggestion.name)
            project.config.project.source_crs = CRSConfig(epsg=suggestion.epsg)
            logger.info(
                "自动识别并补充 CRS: EPSG:%s（%s）", suggestion.epsg, suggestion.reason
            )
        else:
            logger.warning(
                "未能自动识别坐标系（%s），请在“坐标系设置”中指定 CRS，以保证导出坐标正确。",
                suggestion.reason,
            )

    def _autofit_bounds(self, project, holes: Sequence) -> bool:
        """钻孔坐标与工程包围盒严重不符时，按数据范围自动重设（BR-02 / 4.2）。

        默认包围盒（xy 0~1000、z -1000~0）常与真实数据不符：UTM 坐标平面越界，
        或局部坐标但孔口高程为正（Z 越界）。任一情况下若 >99% 钻孔数据不在
        三维包围盒内，则按钻孔范围重设并留 10% 边距；少量越界仍由 WARNING 提示。
        """
        if not holes:
            return False
        bounds = project.config.project.bounds
        collars = np.array([hole.collar_elev for hole in holes], dtype=np.float64)
        bottoms = np.array(
            [
                hole.collar_elev
                - (
                    hole.total_depth
                    or max((iv.depth_bottom for iv in hole.intervals), default=0.0)
                )
                for hole in holes
            ],
            dtype=np.float64,
        )
        xs = np.array([hole.x for hole in holes], dtype=np.float64)
        ys = np.array([hole.y for hole in holes], dtype=np.float64)
        outside = (
            (xs < bounds.min[0]) | (xs > bounds.max[0])
            | (ys < bounds.min[1]) | (ys > bounds.max[1])
            | (collars > bounds.max[2]) | (bottoms < bounds.min[2])
        )
        if outside.mean() <= 0.99:
            return False

        xmin, xmax = _expand(xs.min(), xs.max())
        ymin, ymax = _expand(ys.min(), ys.max())
        zmin, zmax = _expand(min(float(bottoms.min()), float(collars.min())), float(collars.max()))
        project.config.project.bounds = Bounds(
            min=[xmin, ymin, zmin], max=[xmax, ymax, zmax]
        )
        logger.info(
            "工程包围盒与钻孔数据不匹配，已自动重设为 %s ~ %s",
            [round(v, 1) for v in (xmin, ymin, zmin)],
            [round(v, 1) for v in (xmax, ymax, zmax)],
        )
        return True

    def get_quality_report(self) -> QualityReport:
        return self._quality


def _read_faults(path: Path) -> List[Fault]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [Fault.model_validate(item) for item in payload]
    try:
        import fiona
    except Exception as exc:  # noqa: BLE001
        raise GeoModelError(
            ErrorCode.DAT_PAR_001,
            "读取 SHP 需要 fiona/GDAL 支持。",
            context={"path": str(path)},
        ) from exc

    faults: List[Fault] = []
    with fiona.open(path) as source:
        for feature in source:
            props = {key.lower(): value for key, value in feature["properties"].items()}
            trace = _flatten_coords(feature["geometry"])
            params = FaultParams(
                length=float(props.get("length_m") or _trace_length(trace) or 1000.0),
                depth_of_influence=float(props.get("depth_m") or 500.0),
                dip=float(props.get("dip_deg") or 60.0),
                strike=float(props.get("strike_deg") or _strike_from_trace(trace)),
                throw_v=float(props.get("throw_v") or 0.0),
                throw_h=float(props.get("throw_h") or 0.0),
            )
            faults.append(
                Fault(
                    fault_id=str(props.get("fault_id") or props.get("id") or f"F{len(faults)+1}"),
                    name=str(props.get("name") or ""),
                    trace=trace,
                    params=params,
                    side=FaultSide(str(props.get("side") or "unknown")),
                )
            )
    return faults


def _flatten_coords(geometry: Dict) -> List[List[float]]:
    if geometry is None:
        return []
    coords = geometry.get("coordinates") or []

    def _point(pt) -> List[float]:
        # 兼容 2D/3D 坐标（shp 可能带 Z）
        z = float(pt[2]) if len(pt) > 2 else 0.0
        return [float(pt[0]), float(pt[1]), z]

    if geometry.get("type") == "LineString":
        return [_point(pt) for pt in coords]
    if geometry.get("type") == "MultiLineString":
        points: List[List[float]] = []
        for line in coords:
            points.extend(_point(pt) for pt in line)
        return points
    return []


def _trace_length(trace: List[List[float]]) -> float:
    if len(trace) < 2:
        return 0.0
    pts = np.asarray(trace, dtype=float)
    return float(np.linalg.norm(np.diff(pts[:, :2], axis=0), axis=1).sum())


def _strike_from_trace(trace: List[List[float]]) -> float:
    if len(trace) < 2:
        return 0.0
    pts = np.asarray(trace, dtype=float)
    dx = pts[-1, 0] - pts[0, 0]
    dy = pts[-1, 1] - pts[0, 1]
    return float((np.degrees(np.arctan2(dy, dx))) % 360.0)


def _expand(lo: float, hi: float, ratio: float = 0.1):
    """按跨度留边后返回 (lo - pad, hi + pad)，保证 hi > lo。"""
    span = max(float(hi) - float(lo), 1.0)
    pad = span * ratio
    return float(lo) - pad, float(hi) + pad


def _seismic_source(layer, project_source: Optional[CRSConfig]) -> Optional[CRSConfig]:
    """地震层源 CRS：优先层自带，其次回退工程 source_crs（并告警）。"""
    if layer.source_crs_epsg:
        return CRSConfig(epsg=layer.source_crs_epsg)
    if project_source and (project_source.epsg or project_source.proj4 or project_source.wkt):
        logger.warning(
            "地震层 %s 未标注源 CRS，回退按工程 source_crs 变换。",
            layer.name or layer.layer_id,
        )
        return project_source
    return None


def _same_crs(a: Optional[CRSConfig], b: Optional[CRSConfig]) -> bool:
    if a is None or b is None:
        return False
    if a.epsg and b.epsg:
        return a.epsg == b.epsg
    for attr in ("proj4", "wkt"):
        left, right = getattr(a, attr), getattr(b, attr)
        if left and right:
            return left == right
    return False


__all__ = ["DataService", "Severity"]
