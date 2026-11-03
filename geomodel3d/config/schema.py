"""配置数据模型（Pydantic v2）。

集中定义用户级设置与工程级配置的 schema，保证类型校验、可序列化、
可版本化。任何非法参数在进入业务层前即被拦截。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from geomodel3d import __schema_version__

# 已在算法层实现的插值方法；gempy 仅保留为历史工程兼容值，
# 实际建模时会显式报错而非静默降级（见 algorithms/interpolation.py）。
IMPLEMENTED_INTERP_METHODS = ("cokriging", "kriging", "idw", "dsi")

# --------------------------------------------------------------------------- #
# 通用子模型
# --------------------------------------------------------------------------- #


class CRSConfig(BaseModel):
    """坐标系描述（8.5）。"""

    model_config = ConfigDict(extra="forbid")

    epsg: Optional[int] = None
    proj4: Optional[str] = None
    wkt: Optional[str] = None
    name: str = ""

    @field_validator("epsg")
    @classmethod
    def _check_epsg(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and not (1024 <= value <= 999999):
            raise ValueError("EPSG 编码超出合理范围 [1024, 999999]")
        return value


class Bounds(BaseModel):
    """工程包围盒（工程局部/绝对坐标均可，单位与 CRS 一致）。"""

    model_config = ConfigDict(extra="forbid")

    min: List[float] = Field(default_factory=lambda: [0.0, 0.0, -1000.0], min_length=3, max_length=3)
    max: List[float] = Field(default_factory=lambda: [1000.0, 1000.0, 0.0], min_length=3, max_length=3)

    @model_validator(mode="after")
    def _check_order(self) -> "Bounds":
        # 默认值也参与校验（field_validator 对未显式提供的字段不会触发）
        if any(hi <= lo for hi, lo in zip(self.max, self.min)):
            raise ValueError("包围盒 max 必须逐维大于 min")
        return self

    def size(self) -> List[float]:
        return [hi - lo for hi, lo in zip(self.max, self.min)]


class InterpConfig(BaseModel):
    """插值参数（6.1）。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    method: str = "cokriging"
    trend_weight: float = Field(default=0.6, ge=0.0, le=1.0)
    neighbor: int = Field(default=24, ge=1, le=256)
    variogram_model: str = "spherical"
    nugget: float = Field(default=0.0, ge=0.0)
    range_: float = Field(default=1000.0, alias="range", gt=0.0)
    sill: float = Field(default=1.0, gt=0.0)

    @field_validator("method")
    @classmethod
    def _check_method(cls, value: str) -> str:
        allowed = {"cokriging", "kriging", "idw", "dsi", "gempy"}
        if value not in allowed:
            raise ValueError(f"不支持的插值方法: {value}，可选 {sorted(allowed)}")
        return value

    @model_validator(mode="after")
    def _check_variogram(self) -> "InterpConfig":
        if self.nugget >= self.sill:
            raise ValueError("变差函数块金值必须小于基台值 (nugget < sill)")
        return self


class ReserveConfig(BaseModel):
    """储量工业指标默认值（6.4 / BR-04）。"""

    model_config = ConfigDict(extra="forbid")

    body_layers: List[str] = Field(default_factory=list, description="矿体地层代码（剖面 ORE 图层依据）")
    cutoff_grade: float = Field(default=0.0, ge=0.0)
    min_thickness: float = Field(default=0.0, ge=0.0)
    max_waste_thickness: float = Field(default=0.0, ge=0.0)
    density: float = Field(default=2.6, gt=0.0)
    moisture: float = Field(default=0.0, ge=0.0, lt=1.0)
    recovery: float = Field(default=1.0, gt=0.0, le=1.0)
    grade_unit: str = "%"


class StratigraphyEntry(BaseModel):
    """地层字典条目（3.3）。"""

    model_config = ConfigDict(extra="forbid")

    layer_code: str
    name: str = ""
    order: int = Field(ge=0)
    color: str = "#CCCCCC"
    alpha: float = Field(default=0.9, ge=0.0, le=1.0)


# --------------------------------------------------------------------------- #
# 工程配置
# --------------------------------------------------------------------------- #


class ProjectMeta(BaseModel):
    """工程元信息。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = "untitled"
    crs: CRSConfig = Field(default_factory=CRSConfig)
    source_crs: Optional[CRSConfig] = None
    bounds: Bounds = Field(default_factory=Bounds)
    origin: List[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0], min_length=3, max_length=3)


class ProjectConfig(BaseModel):
    """工程级配置（*.gm3d 内 config.json）。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: str = __schema_version__
    project: ProjectMeta
    stratigraphy: List[StratigraphyEntry] = Field(default_factory=list)
    engine_policy: str = "auto"
    interp: InterpConfig = Field(default_factory=InterpConfig)
    reserve: ReserveConfig = Field(default_factory=ReserveConfig)
    autosave_minutes: int = Field(default=10, ge=0)
    versions_max: int = Field(default=20, ge=1)

    @field_validator("engine_policy")
    @classmethod
    def _check_policy(cls, value: str) -> str:
        allowed = {"auto", "gempy", "opengeode", "mock"}
        if value not in allowed:
            raise ValueError(f"engine_policy 必须属于 {sorted(allowed)}")
        return value

    @model_validator(mode="after")
    def _check_stratigraphy(self) -> "ProjectConfig":
        codes = [entry.layer_code for entry in self.stratigraphy]
        orders = [entry.order for entry in self.stratigraphy]
        if len(set(codes)) != len(codes):
            raise ValueError("地层字典存在重复的 layer_code")
        if len(set(orders)) != len(orders):
            raise ValueError("地层字典存在重复的 order")
        return self


# --------------------------------------------------------------------------- #
# 用户设置
# --------------------------------------------------------------------------- #


class AppSettings(BaseModel):
    """用户级设置（settings.json，3.7）。"""

    model_config = ConfigDict(extra="forbid")

    theme: str = "light"
    language: str = "zh_CN"
    log_level: str = "INFO"
    default_directory: str = ""
    default_crs_epsg: Optional[int] = None
    autosave_minutes: int = Field(default=10, ge=0)
    versions_max: int = Field(default=20, ge=1)
    recent_projects: List[str] = Field(default_factory=list)
    show_axes: bool = True
    background: str = "auto"
    layout_state: Dict[str, str] = Field(default_factory=dict)

    @field_validator("theme")
    @classmethod
    def _check_theme(cls, value: str) -> str:
        if value not in {"light", "dark"}:
            raise ValueError("theme 仅支持 light / dark")
        return value

    @field_validator("log_level")
    @classmethod
    def _check_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"log_level 必须属于 {sorted(allowed)}")
        return upper

    @field_validator("default_crs_epsg")
    @classmethod
    def _check_default_epsg(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and not (1024 <= value <= 999999):
            raise ValueError("EPSG 编码超出合理范围 [1024, 999999]")
        return value

    def push_recent(self, path: str, limit: int = 10) -> None:
        """把工程路径置顶到最近列表。"""
        self.recent_projects = [path] + [p for p in self.recent_projects if p != path]
        del self.recent_projects[limit:]


__all__ = [
    "CRSConfig",
    "Bounds",
    "InterpConfig",
    "ReserveConfig",
    "StratigraphyEntry",
    "ProjectMeta",
    "ProjectConfig",
    "AppSettings",
    "IMPLEMENTED_INTERP_METHODS",
]
