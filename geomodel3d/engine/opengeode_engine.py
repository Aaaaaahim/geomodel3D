"""OpenGeode 引擎适配器（高精度拓扑 / 封闭实体 / 原生网格 IO）。

OpenGeode-core（MIT）为可选依赖。适配器在分层建模结果之上，构造真实的
``HybridSolid3D`` 六面体实体、写入地层属性并计算单元邻接（拓扑），
支持导出 OpenGeode 原生格式 ``.og_hso3d``。

说明：断层落差在分层建模阶段以位移方式体现；OpenGeode-core 本身不含
布尔切割算子（需额外的 geode/simplex 扩展），拓扑级切割列为后续工作。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np

from geomodel3d.config.errors import EngineUnavailableError, ErrorCode, GeoModelError
from geomodel3d.engine.base import EngineCapabilities, ModelingEngine, ModelingSpec
from geomodel3d.engine.layered import build_layered_mesh
from geomodel3d.engine.umesh import UMesh

logger = logging.getLogger(__name__)

NATIVE_EXTENSION = "og_hso3d"


def opengeode_available() -> bool:
    try:
        import opengeode  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _create_polyhedron(builder, conn) -> None:
    """按单元顶点数创建 OpenGeode 多面体（四面体/金字塔/棱柱/六面体）。"""
    vertices = [int(i) for i in conn]
    size = len(vertices)
    if size == 4:
        builder.create_tetrahedron(vertices)
    elif size == 5:
        builder.create_pyramid(vertices)
    elif size == 6:
        builder.create_prism(vertices)
    elif size == 8:
        builder.create_hexahedron(vertices)
    else:
        raise GeoModelError(
            ErrorCode.MDL_ENG_001,
            f"不支持的 OpenGeode 单元顶点数: {size}",
        )


def to_hybrid_solid(mesh: UMesh):
    """把 UMesh 转换为 OpenGeode ``HybridSolid3D``（含 layer 属性）。"""
    import opengeode as og

    solid = og.HybridSolid3D.create()
    builder = og.HybridSolidBuilder3D.create(solid)
    builder.create_vertices(mesh.n_points)
    for index in range(mesh.n_points):
        x, y, z = mesh.vertices[index]
        builder.set_point(index, og.Point3D([float(x), float(y), float(z)]))
    for conn in mesh.cells:
        _create_polyhedron(builder, conn)
    builder.compute_polyhedron_adjacencies()

    if "layer" in mesh.cell_attr:
        attribute = solid.polyhedron_attribute_manager().find_or_create_attribute_variable_int(
            "layer", 0
        )
        for index, value in enumerate(mesh.cell_attr["layer"]):
            attribute.set_value(index, int(value))
    return solid


def from_hybrid_solid(solid, meta: Optional[dict] = None) -> UMesh:
    """把 OpenGeode ``HybridSolid3D`` 转回 UMesh（六面体）。"""
    import opengeode as og

    from geomodel3d.config.errors import ErrorCode, GeoModelError
    from geomodel3d.engine.umesh import (
        VTK_HEXAHEDRON,
        VTK_PYRAMID,
        VTK_TETRA,
        VTK_WEDGE,
    )

    _TYPE_BY_SIZE = {
        4: VTK_TETRA,
        5: VTK_PYRAMID,
        6: VTK_WEDGE,
        8: VTK_HEXAHEDRON,
    }
    vertices = []
    for index in range(solid.nb_vertices()):
        point = solid.point(index)
        vertices.append([point.value(0), point.value(1), point.value(2)])
    vertices = np.asarray(vertices, dtype=np.float64)
    cells = []
    types = []
    for index in range(solid.nb_polyhedra()):
        conn = [int(v) for v in solid.polyhedron_vertices(index)]
        cell_type = _TYPE_BY_SIZE.get(len(conn))
        if cell_type is None:
            raise GeoModelError(
                ErrorCode.MDL_ENG_001,
                f"不支持的 OpenGeode 单元顶点数: {len(conn)}",
            )
        cells.append(np.asarray(conn, dtype=np.int64))
        types.append(cell_type)
    mesh = UMesh(
        vertices=vertices,
        cells=cells,
        cell_types=np.asarray(types, dtype=np.int32),
        meta=meta or {"engine": "opengeode"},
    )
    mesh.opengeode_solid = solid  # type: ignore[attr-defined]
    return mesh


def save_native(mesh: UMesh, path: Path) -> Path:
    """导出 OpenGeode 原生实体格式。"""
    import opengeode as og

    solid = getattr(mesh, "opengeode_solid", None)
    if solid is None:
        solid = to_hybrid_solid(mesh)
    path = Path(path)
    if path.suffix.lstrip(".").lower() != NATIVE_EXTENSION:
        path = path.with_suffix("." + NATIVE_EXTENSION)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not og.is_hybrid_solid_saveable3D(solid, str(path)):
        raise GeoModelError(ErrorCode.EXP_CHK_001, f"OpenGeode 无法保存到 {path}")
    og.save_hybrid_solid3D(solid, str(path))
    return path


class OpenGeodeEngine(ModelingEngine):
    name = "opengeode"
    capabilities = EngineCapabilities(
        name="opengeode",
        high_precision=True,
        fault_cut=False,
        fault_displacement=True,
        closed_solids=True,
        smooth_surfaces=False,
        available=False,
    )

    def is_available(self) -> bool:
        return opengeode_available()

    def build(self, spec: ModelingSpec, ctx) -> UMesh:
        if not self.is_available():
            raise EngineUnavailableError(
                "OpenGeode 未安装，无法执行高精度建模。",
                context={"engine": self.name},
            )
        ctx.progress(5, "OpenGeode 初始化")
        mesh = build_layered_mesh(spec, ctx, engine_name=self.name)
        ctx.progress(85, "构建 OpenGeode 实体与拓扑")
        solid = to_hybrid_solid(mesh)
        mesh.opengeode_solid = solid  # type: ignore[attr-defined]
        mesh.meta["opengeode"] = {
            "native_extension": NATIVE_EXTENSION,
            "nb_polyhedra": int(solid.nb_polyhedra()),
            "nb_vertices": int(solid.nb_vertices()),
            "border_facets": _border_facets(solid),
        }
        ctx.progress(100, "完成")
        return mesh

    def rebuild_local(self, mesh: UMesh, spec: ModelingSpec, ctx) -> UMesh:
        if spec.region is None or not self.is_available():
            return self.build(spec, ctx)
        from geomodel3d.algorithms.local_rebuild import rebuild_local_layered

        ctx.progress(10, "局部重建模")
        updated = rebuild_local_layered(mesh, spec, ctx)
        ctx.progress(85, "重建 OpenGeode 实体")
        solid = to_hybrid_solid(updated)
        updated.opengeode_solid = solid  # type: ignore[attr-defined]
        updated.meta["opengeode"] = {
            "native_extension": NATIVE_EXTENSION,
            "nb_polyhedra": int(solid.nb_polyhedra()),
            "nb_vertices": int(solid.nb_vertices()),
            "border_facets": _border_facets(solid),
        }
        return updated


def _border_facets(solid) -> int:
    """统计位于实体外表面的面片数（封闭实体应构成闭合外表面）。"""
    try:
        return int(
            sum(
                solid.nb_polyhedron_facets_on_border(index)
                for index in range(solid.nb_polyhedra())
            )
        )
    except Exception:  # noqa: BLE001
        logger.warning("统计 OpenGeode 外表面面片失败", exc_info=True)
        return 0


__all__ = [
    "OpenGeodeEngine",
    "opengeode_available",
    "to_hybrid_solid",
    "from_hybrid_solid",
    "save_native",
    "NATIVE_EXTENSION",
]
