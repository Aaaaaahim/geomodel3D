"""领域层：实体、枚举、业务规则与方案/结果模型（纯 Python）。"""
from geomodel3d.domain.entities import (
    Drillhole,
    DrillholeInterval,
    EngineKind,
    Fault,
    FaultParams,
    FaultSide,
    ModelRunMeta,
    ReserveClass,
    RunStatus,
    SectionKind,
    SeismicLayer,
)
from geomodel3d.domain.project import Project
from geomodel3d.domain.reserve import (
    ReserveBlock,
    ReserveClassSummary,
    ReserveMetrics,
    ReserveResult,
)
from geomodel3d.domain.rules import QualityIssue, QualityReport, Severity, validate_boreholes
from geomodel3d.domain.section import (
    SectionBorehole,
    SectionInterval,
    SectionPlan,
    SectionResult,
)

__all__ = [
    "Drillhole",
    "DrillholeInterval",
    "EngineKind",
    "Fault",
    "FaultParams",
    "FaultSide",
    "ModelRunMeta",
    "ReserveClass",
    "RunStatus",
    "SectionKind",
    "SeismicLayer",
    "Project",
    "ReserveBlock",
    "ReserveClassSummary",
    "ReserveMetrics",
    "ReserveResult",
    "QualityIssue",
    "QualityReport",
    "Severity",
    "validate_boreholes",
    "SectionBorehole",
    "SectionInterval",
    "SectionPlan",
    "SectionResult",
]
