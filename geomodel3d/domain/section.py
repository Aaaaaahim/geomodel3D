"""剖面领域模型（M5 / 6.5）。

剖面方案（任意线 / 平行勘探线 / 水平切面）与剖面结果
（二维展开坐标 + 真实三维坐标 + 钻孔投影）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from geomodel3d.domain.entities import SectionKind


class SectionPlan(BaseModel):
    """剖面方案。"""

    model_config = ConfigDict(extra="forbid")

    name: str = "section"
    kind: SectionKind = SectionKind.ARBITRARY
    origin: List[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    u_axis: List[float] = Field(default_factory=lambda: [1.0, 0.0, 0.0])
    v_axis: List[float] = Field(default_factory=lambda: [0.0, 0.0, 1.0])
    width: float = 1000.0
    height: float = 1000.0
    crs_epsg: Optional[int] = None
    show_layers: List[str] = Field(default_factory=list)
    show_boreholes: bool = True
    borehole_offset_window: float = 50.0
    scale: float = 2000.0
    dpi: int = 150
    spacing: float = 200.0
    count: int = 5


class SectionInterval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_code: str
    top_v: float
    base_v: float
    grade: Optional[float] = None


class SectionBorehole(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hole_id: str
    u: float
    collar_v: float
    offset: float = 0.0
    intervals: List[SectionInterval] = Field(default_factory=list)


class SectionResult(BaseModel):
    """剖面结果：二维展开坐标 + 真实大地坐标。"""

    model_config = ConfigDict(extra="forbid")

    plan: SectionPlan
    polylines: Dict[str, List[List[List[float]]]] = Field(default_factory=dict)
    world_polylines: Dict[str, List[List[List[float]]]] = Field(default_factory=dict)
    fault_traces: Dict[str, List[List[float]]] = Field(default_factory=dict)
    fault_labels: List[Dict[str, Any]] = Field(default_factory=list)
    boreholes: List[SectionBorehole] = Field(default_factory=list)
    ore_layers: List[str] = Field(default_factory=list, description="矿体地层代码（ORE 图层判定）")
    bounds_uv: List[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    created: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    warnings: List[str] = Field(default_factory=list)

    def annotations(self, *, min_thickness: float = 0.05) -> List[Dict[str, Any]]:
        """生成剖面标注：地层名、钻孔分层厚度、断层编号。"""
        items: List[Dict[str, Any]] = []
        for code, polylines in self.polylines.items():
            if not polylines:
                continue
            representative = max(polylines[0], key=lambda point: point[0])
            items.append(
                {"text": code, "u": representative[0], "v": representative[1], "kind": "layer"}
            )
        for hole in self.boreholes:
            for interval in hole.intervals:
                thickness = abs(interval.top_v - interval.base_v)
                if thickness >= min_thickness:
                    items.append(
                        {
                            "text": f"{thickness:.1f}",
                            "u": hole.u,
                            "v": (interval.top_v + interval.base_v) / 2.0,
                            "kind": "thickness",
                        }
                    )
        for label in self.fault_labels:
            items.append(
                {
                    "text": str(label.get("fault_id", "")),
                    "u": label.get("u", 0.0),
                    "v": label.get("v", 0.0),
                    "kind": "fault",
                }
            )
        return items


__all__ = ["SectionPlan", "SectionInterval", "SectionBorehole", "SectionResult", "SectionKind"]
