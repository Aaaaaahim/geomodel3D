"""算法层：插值、几何、储量、剖面、网格体检与修复、统计（headless 可测）。"""
from geomodel3d.algorithms.geometry import (
    fault_impact_region,
    region_contains,
    region_intersection,
)
from geomodel3d.algorithms.interpolation import idw, kriging
from geomodel3d.algorithms.local_rebuild import rebuild_local_layered
from geomodel3d.algorithms.mesh_health import HealthReport, health_check
from geomodel3d.algorithms.repair import RepairOptions, RepairReport, repair
from geomodel3d.algorithms.reserve import estimate_reserves
from geomodel3d.algorithms.section import (
    build_section,
    make_arbitrary_section,
    make_exploration_sections,
    make_horizontal_section,
    section_azimuth,
)
from geomodel3d.algorithms.statistics import (
    fault_statistics,
    grade_statistics,
    quality_statistics,
    thickness_statistics,
)

__all__ = [
    "idw",
    "kriging",
    "fault_impact_region",
    "region_intersection",
    "region_contains",
    "estimate_reserves",
    "health_check",
    "HealthReport",
    "rebuild_local_layered",
    "repair",
    "RepairOptions",
    "RepairReport",
    "build_section",
    "make_arbitrary_section",
    "make_horizontal_section",
    "make_exploration_sections",
    "section_azimuth",
    "thickness_statistics",
    "grade_statistics",
    "fault_statistics",
    "quality_statistics",
]
