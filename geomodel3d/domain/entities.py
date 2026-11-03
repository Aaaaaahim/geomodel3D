"""领域实体与枚举（3.3）。

纯 Python 定义，不依赖 Qt / 引擎，便于 headless 调用与单元测试。
"""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class EngineKind(str, Enum):
    GEMPY = "gempy"
    OPENGEODE = "opengeode"
    MOCK = "mock"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FaultSide(str, Enum):
    NORMAL = "normal"       # 正断层
    REVERSE = "reverse"     # 逆断层
    STRIKE_SLIP = "strike"  # 平移断层
    UNKNOWN = "unknown"


class SectionKind(str, Enum):
    ARBITRARY = "arbitrary"   # 任意手绘线
    EXPLORATION = "exploration"  # 平行勘探线
    HORIZONTAL = "horizontal"    # 水平切面


class ReserveClass(str, Enum):
    MEASURED = "331"      # 探明
    INDICATED = "332"     # 控制
    INFERRED = "333"      # 推断


# --------------------------------------------------------------------------- #
# 钻孔
# --------------------------------------------------------------------------- #


class DrillholeInterval(BaseModel):
    """钻孔分层区间。"""

    model_config = ConfigDict(extra="forbid")

    layer_code: str
    depth_top: float = Field(ge=0.0)
    depth_bottom: float = Field(ge=0.0)
    thickness: Optional[float] = None
    grade: Optional[float] = None
    grade_unit: str = "%"

    @field_validator("depth_bottom")
    @classmethod
    def _check_depth(cls, value: float, info) -> float:
        top = info.data.get("depth_top")
        if top is not None and value < top:
            raise ValueError("层底深不得小于层顶深")
        return value

    @model_validator(mode="after")
    def _check_thickness(self) -> "DrillholeInterval":
        derived = round(self.depth_bottom - self.depth_top, 6)
        if self.thickness is None:
            self.thickness = derived
        elif abs(self.thickness - derived) > 1e-3:
            raise ValueError(
                f"厚度 {self.thickness} 与层顶/底深之差 {derived} 不一致"
            )
        return self


class Drillhole(BaseModel):
    """钻孔（含 provenance 以留痕处置动作，4.2）。"""

    model_config = ConfigDict(extra="forbid")

    hole_id: str
    x: float
    y: float
    collar_elev: float
    total_depth: float = 0.0
    intervals: List[DrillholeInterval] = Field(default_factory=list)
    provenance: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("total_depth")
    @classmethod
    def _check_total(cls, value: float) -> float:
        if value < 0:
            raise ValueError("孔深不得为负")
        return value

    def layer_codes(self) -> List[str]:
        return [iv.layer_code for iv in self.intervals]


class SeismicLayer(BaseModel):
    """地震趋势面（归一化后的元数据 + 采样点）。"""

    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(default_factory=_new_id)
    name: str = ""
    kind: str = "trend"
    points: List[List[float]] = Field(default_factory=list)  # [[x, y, z], ...]
    source_crs_epsg: Optional[int] = None
    smooth: float = Field(default=0.0, ge=0.0)

    @field_validator("points")
    @classmethod
    def _check_points(cls, value: List[List[float]]) -> List[List[float]]:
        for point in value:
            if len(point) != 3:
                raise ValueError("地震点坐标必须为 [x, y, z] 三维")
        return value


# --------------------------------------------------------------------------- #
# 断层
# --------------------------------------------------------------------------- #


class FaultParams(BaseModel):
    """断层参数化产状（6.3）。"""

    model_config = ConfigDict(extra="forbid")

    length: float = Field(default=1000.0, gt=0.0, description="延伸长度 L (m)")
    depth_of_influence: float = Field(default=500.0, gt=0.0, description="最大影响深度 D (m)")
    dip: float = Field(default=60.0, ge=0.0, le=90.0, description="倾角 (°)")
    strike: float = Field(default=0.0, ge=0.0, lt=360.0, description="走向 (°)")
    throw_v: float = Field(default=0.0, ge=0.0, description="垂直落差 (m)")
    throw_h: float = Field(default=0.0, ge=0.0, description="水平落差 (m)")
    buffer: float = Field(default=50.0, ge=0.0, description="横向缓冲 Δ (m)")

    @field_validator("dip")
    @classmethod
    def _check_dip(cls, value: float) -> float:
        if not 0.0 <= value <= 90.0:
            raise ValueError("倾角必须位于 [0, 90] 度")
        return value


class Fault(BaseModel):
    """断层：几何迹线 + 参数化产状。"""

    model_config = ConfigDict(extra="forbid")

    fault_id: str = Field(default_factory=_new_id)
    name: str = ""
    trace: List[List[float]] = Field(default_factory=list)  # 走向迹线 [[x,y,z], ...]
    params: FaultParams = Field(default_factory=FaultParams)
    side: FaultSide = FaultSide.UNKNOWN
    status: str = "active"
    provenance: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("trace")
    @classmethod
    def _check_trace(cls, value: List[List[float]]) -> List[List[float]]:
        for point in value:
            if len(point) != 3:
                raise ValueError("断层迹线坐标必须为 [x, y, z] 三维")
        return value


# --------------------------------------------------------------------------- #
# 建模运行记录
# --------------------------------------------------------------------------- #


class ModelRunMeta(BaseModel):
    """一次建模/重建模的可追溯元数据（3.3）。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(default_factory=_new_id)
    engine: EngineKind = EngineKind.MOCK
    status: RunStatus = RunStatus.PENDING
    params_snapshot: Dict[str, Any] = Field(default_factory=dict)
    input_hash: str = ""
    region: Optional[List[float]] = None  # 局部重建模影响域 [xmin,ymin,zmin,xmax,ymax,zmax]
    duration_s: float = 0.0
    message: str = ""


__all__ = [
    "EngineKind",
    "RunStatus",
    "FaultSide",
    "SectionKind",
    "ReserveClass",
    "DrillholeInterval",
    "Drillhole",
    "SeismicLayer",
    "FaultParams",
    "Fault",
    "ModelRunMeta",
]
