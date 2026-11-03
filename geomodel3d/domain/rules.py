"""业务规则与数据质量校验（BR-02、4.2）。

规则引擎逐条记录问题分级（错误/告警/提示），处置动作留痕。
所有硬错误清零后才允许进入建模。
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from geomodel3d.domain.entities import Drillhole


class Severity(str, Enum):
    ERROR = "error"      # 阻断
    WARNING = "warning"  # 不阻断，给处置
    INFO = "info"


class QualityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Severity
    code: str
    message: str
    item_id: str = ""
    action: str = ""  # 处置动作（修复/剔除/忽略），留痕


class QualityReport(BaseModel):
    """数据质量报告（可导出 PDF/Excel，4.2）。"""

    model_config = ConfigDict(extra="forbid")

    issues: List[QualityIssue] = Field(default_factory=list)
    metrics: Dict[str, float] = Field(default_factory=dict)

    @property
    def errors(self) -> List[QualityIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> List[QualityIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def passed(self) -> bool:
        return not self.errors

    def add(self, severity: Severity, code: str, message: str, item_id: str = "") -> None:
        self.issues.append(
            QualityIssue(severity=severity, code=code, message=message, item_id=item_id)
        )

    def summary(self) -> Dict[str, int]:
        return {
            "error": len(self.errors),
            "warning": len(self.warnings),
            "info": len([i for i in self.issues if i.severity == Severity.INFO]),
        }


def validate_boreholes(
    holes: Sequence[Drillhole],
    *,
    valid_layers: Optional[Iterable[str]] = None,
    grade_range: Optional[tuple] = None,
    bounds: Optional[Sequence[Sequence[float]]] = None,
) -> QualityReport:
    """校验钻孔集合，返回质量报告。

    Args:
        holes: 钻孔列表。
        valid_layers: 受控地层字典代码集合。
        grade_range: 品位允许区间 ``(min, max)``。
        bounds: 工程包围盒 ``[[xmin,ymin,zmin],[xmax,ymax,zmax]]``。
    """
    report = QualityReport()
    layer_set = set(valid_layers) if valid_layers else None
    seen_ids: Dict[str, int] = {}

    for hole in holes:
        seen_ids[hole.hole_id] = seen_ids.get(hole.hole_id, 0) + 1

        if bounds is not None:
            (xmin, ymin, zmin), (xmax, ymax, zmax) = bounds
            if not (xmin <= hole.x <= xmax and ymin <= hole.y <= ymax):
                report.add(
                    Severity.WARNING,
                    "DAT-VAL-BND",
                    f"钻孔 {hole.hole_id} 孔口平面坐标越界",
                    hole.hole_id,
                )
            if not (zmin <= hole.collar_elev <= zmax):
                report.add(
                    Severity.WARNING,
                    "DAT-VAL-ELV",
                    f"钻孔 {hole.hole_id} 孔口高程越界",
                    hole.hole_id,
                )

        prev_bottom: Optional[float] = None
        for index, interval in enumerate(hole.intervals):
            if interval.depth_bottom <= interval.depth_top:
                report.add(
                    Severity.ERROR,
                    "DAT-VAL-THB",
                    f"钻孔 {hole.hole_id} 第 {index + 1} 层厚度不大于 0",
                    hole.hole_id,
                )
            if prev_bottom is not None and interval.depth_top < prev_bottom - 1e-6:
                report.add(
                    Severity.ERROR,
                    "DAT-VAL-ORD",
                    f"钻孔 {hole.hole_id} 第 {index + 1} 层层序倒置/重叠",
                    hole.hole_id,
                )
            prev_bottom = interval.depth_bottom

            if layer_set is not None and interval.layer_code not in layer_set:
                report.add(
                    Severity.ERROR,
                    "DAT-VAL-LYR",
                    f"钻孔 {hole.hole_id} 地层代码 {interval.layer_code} 不在受控字典",
                    hole.hole_id,
                )

            if interval.grade is not None and grade_range is not None:
                lo, hi = grade_range
                if not (lo <= interval.grade <= hi):
                    report.add(
                        Severity.WARNING,
                        "DAT-VAL-GRD",
                        f"钻孔 {hole.hole_id} 品位 {interval.grade} 越界 [{lo}, {hi}]",
                        hole.hole_id,
                    )

        if hole.total_depth and hole.intervals:
            deepest = max(iv.depth_bottom for iv in hole.intervals)
            if deepest > hole.total_depth + 1e-6:
                report.add(
                    Severity.ERROR,
                    "DAT-VAL-DPT",
                    f"钻孔 {hole.hole_id} 分层深度超过孔深",
                    hole.hole_id,
                )

    for hole_id, count in seen_ids.items():
        if count > 1:
            report.add(Severity.ERROR, "DAT-VAL-DUP", f"钻孔号重复: {hole_id}", hole_id)

    total = max(len(holes), 1)
    report.metrics["hole_count"] = float(len(holes))
    report.metrics["issue_rate"] = round(len(report.issues) / total, 4)
    return report


def derive_stratigraphy(holes: Sequence[Drillhole]) -> List["StratigraphyEntry"]:
    """从钻孔分层自动推导地层字典（按平均顶深由浅到深排序）。"""
    from geomodel3d.config.schema import StratigraphyEntry

    depths: Dict[str, List[float]] = {}
    for hole in holes:
        for interval in hole.intervals:
            depths.setdefault(interval.layer_code, []).append(interval.depth_top)
    if not depths:
        return []
    ordered = sorted(depths, key=lambda code: sum(depths[code]) / len(depths[code]))
    palette = ["#D9CBB8", "#7FA8C9", "#6B7A8D", "#E8913A", "#2F9E63", "#9B7FB0", "#C98FB0", "#8FB08F"]
    return [
        StratigraphyEntry(
            layer_code=code,
            name=code,
            order=index,
            color=palette[index % len(palette)],
        )
        for index, code in enumerate(ordered)
    ]


__all__ = ["Severity", "QualityIssue", "QualityReport", "validate_boreholes", "derive_stratigraphy"]
