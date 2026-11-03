"""储量计算领域模型（M4 / 6.4 / BR-04 / BR-05）。

工业指标、块段、分级汇总与结果对象，纯 Python 可序列化，
供算法、服务、报告导出与界面共用。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from geomodel3d.domain.entities import ReserveClass


class ReserveMetrics(BaseModel):
    """储量计算工业指标与口径（BR-04 / BR-05）。"""

    model_config = ConfigDict(extra="forbid")

    mineral: str = "coal"
    body_layers: List[str] = Field(default_factory=list, description="矿体所属地层代码（空表示未指定，将告警且不计储量）")
    method: str = Field(default="volume", description="volume | block")
    density: float = Field(default=2.6, gt=0.0, description="矿石体重 t/m³")
    cutoff_grade: float = Field(default=0.0, ge=0.0, description="边界品位")
    min_thickness: float = Field(default=0.0, ge=0.0, description="最小可采厚度 m")
    max_waste_thickness: float = Field(default=0.0, ge=0.0, description="夹石剔除厚度 m")
    moisture: float = Field(default=0.0, ge=0.0, lt=1.0)
    recovery: float = Field(default=1.0, gt=0.0, le=1.0)
    grade_unit: str = "%"
    measured_distance: float = Field(default=150.0, gt=0.0, description="探明(331)最大工程间距 m")
    indicated_distance: float = Field(default=300.0, gt=0.0, description="控制(332)最大工程间距 m")
    block_size: List[float] = Field(default_factory=lambda: [50.0, 50.0, 20.0])
    level_height: float = Field(default=50.0, gt=0.0, description="中段高度 m")

    @field_validator("method")
    @classmethod
    def _check_method(cls, value: str) -> str:
        if value not in {"volume", "block"}:
            raise ValueError("method 仅支持 volume / block")
        return value

    @field_validator("block_size")
    @classmethod
    def _check_block_size(cls, value: List[float]) -> List[float]:
        if len(value) != 3 or any(v <= 0 for v in value):
            raise ValueError("block_size 必须是三个正数 [dx, dy, dz]")
        return value

    @model_validator(mode="after")
    def _check_distances(self) -> "ReserveMetrics":
        if self.indicated_distance < self.measured_distance:
            raise ValueError("控制间距不得小于探明间距")
        return self


class ReserveBlock(BaseModel):
    """块段明细。"""

    model_config = ConfigDict(extra="forbid")

    block_id: str
    center: List[float]
    volume: float
    tonnage: float
    grade: float
    thickness: float
    reserve_class: ReserveClass
    layer_code: str = ""
    level: float = 0.0
    drillhole_distance: float = 0.0


class ReserveClassSummary(BaseModel):
    """分级汇总。"""

    model_config = ConfigDict(extra="forbid")

    reserve_class: ReserveClass
    block_count: int = 0
    volume: float = 0.0
    tonnage: float = 0.0
    metal: float = 0.0
    avg_grade: float = 0.0
    avg_thickness: float = 0.0


class ReserveResult(BaseModel):
    """储量计算结果。"""

    model_config = ConfigDict(extra="forbid")

    body_id: str = ""
    method: str = "volume"
    metrics: ReserveMetrics = Field(default_factory=ReserveMetrics)
    total_volume: float = 0.0
    total_tonnage: float = 0.0
    total_metal: float = 0.0
    avg_grade: float = 0.0
    avg_thickness: float = 0.0
    by_class: Dict[str, ReserveClassSummary] = Field(default_factory=dict)
    by_level: List[Dict[str, float]] = Field(default_factory=list)
    blocks: List[ReserveBlock] = Field(default_factory=list)
    created: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    warnings: List[str] = Field(default_factory=list)

    def summary_text(self) -> str:
        return (
            f"体积 {self.total_volume:,.0f} m³ · 矿石量 {self.total_tonnage:,.0f} t · "
            f"金属量 {self.total_metal:,.2f} · 平均品位 {self.avg_grade:.3f}"
        )


__all__ = [
    "ReserveMetrics",
    "ReserveBlock",
    "ReserveClassSummary",
    "ReserveResult",
    "ReserveClass",
]
