"""成果导出器（M8 / 8.6）。

三维模型（VTU/OBJ/STL/GLB/PLY）与二维剖面（SHP/DXF/GeoJSON/SVG/PNG）。
图层命名遵循 8.6 规范：``GEOL_<stratum>``、``ORE_<body>``、``FAULT_<id>``、
``BORE_<hole>``、``SECTION_<no>``。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from geomodel3d.config.errors import ErrorCode, GeoModelError
from geomodel3d.domain.section import SectionResult
from geomodel3d.engine.umesh import UMesh

logger = logging.getLogger(__name__)


def layer_name(code: str, kind: str = "GEOL") -> str:
    return f"{kind}_{code}"


# --------------------------------------------------------------------------- #
# 三维模型
# --------------------------------------------------------------------------- #


def export_mesh(mesh: UMesh, path: Path, fmt: Optional[str] = None) -> Path:
    """导出三维网格（VTU/OBJ/STL/GLB/PLY；OpenGeode 原生 ``og_hso3d``）。"""
    path = Path(path)
    fmt = (fmt or path.suffix.lstrip(".")).lower()
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "og_hso3d":
        from geomodel3d.engine.opengeode_engine import save_native

        return save_native(mesh, path)
    grid = mesh.to_pyvista()
    try:
        if fmt in ("obj", "stl", "ply", "glb"):
            surface = grid.extract_surface() if grid.n_cells else grid
            surface.save(str(path))
        elif fmt == "vtu":
            grid.save(str(path))
        else:
            raise GeoModelError(ErrorCode.EXP_CHK_001, f"不支持的三维格式: {fmt}")
    except GeoModelError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise GeoModelError(ErrorCode.GEN_IO_001, f"导出三维模型失败: {exc}") from exc
    logger.info("三维模型已导出: %s", path)
    return path


# --------------------------------------------------------------------------- #
# 二维剖面
# --------------------------------------------------------------------------- #


def export_geojson(result: SectionResult, path: Path) -> Path:
    features: List[dict] = []
    for code, polylines in result.world_polylines.items():
        for index, line in enumerate(polylines):
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": line},
                    "properties": {
                        "layer": code,
                        "section": result.plan.name,
                        "index": index,
                        "crs_epsg": result.plan.crs_epsg,
                    },
                }
            )
    for fault_id, trace in result.fault_traces.items():
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": _fault_world(result, trace)},
                "properties": {
                    "layer": layer_name(fault_id, "FAULT"),
                    "section": result.plan.name,
                    "crs_epsg": result.plan.crs_epsg,
                },
            }
        )
    payload = {
        "type": "FeatureCollection",
        "name": result.plan.name,
        "crs": {"type": "name", "properties": {"name": f"EPSG:{result.plan.crs_epsg}"}},
        "features": features,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("剖面 GeoJSON 已导出: %s", path)
    return path


def export_shp(result: SectionResult, path: Path) -> Path:
    try:
        import fiona
        from fiona.crs import CRS as FionaCRS
    except Exception as exc:  # noqa: BLE001
        raise GeoModelError(ErrorCode.EXP_CHK_001, "导出 SHP 需要 fiona/GDAL。") from exc

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = {
        "geometry": "LineString",
        "properties": {"layer": "str", "section": "str", "index": "int"},
    }
    crs = FionaCRS.from_epsg(result.plan.crs_epsg) if result.plan.crs_epsg else None
    with fiona.open(path, "w", driver="ESRI Shapefile", schema=schema, crs=crs) as sink:
        for code, polylines in result.world_polylines.items():
            for index, line in enumerate(polylines):
                sink.write(
                    {
                        "geometry": {"type": "LineString", "coordinates": line},
                        "properties": {"layer": code, "section": result.plan.name, "index": index},
                    }
                )
        for fault_id, trace in result.fault_traces.items():
            sink.write(
                {
                    "geometry": {"type": "LineString", "coordinates": _fault_world(result, trace)},
                    "properties": {"layer": layer_name(fault_id, "FAULT"), "section": result.plan.name, "index": 0},
                }
            )
    logger.info("剖面 SHP 已导出: %s", path)
    return path


def export_dxf(result: SectionResult, path: Path) -> Path:
    try:
        import ezdxf
    except Exception as exc:  # noqa: BLE001
        raise GeoModelError(ErrorCode.EXP_CHK_001, "导出 DXF 需要 ezdxf。") from exc

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    ore = set(result.ore_layers)
    for code, polylines in result.world_polylines.items():
        name = layer_name(code, "ORE" if code in ore else "GEOL")
        if name not in doc.layers:
            doc.layers.add(name)
        for line in polylines:
            msp.add_polyline3d(line, close=False, dxfattribs={"layer": name})
    for fault_id, trace in result.fault_traces.items():
        name = layer_name(fault_id, "FAULT")
        if name not in doc.layers:
            doc.layers.add(name, color=30)
        msp.add_polyline3d(_fault_world(result, trace), close=False, dxfattribs={"layer": name})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    logger.info("剖面 DXF 已导出: %s", path)
    return path


def export_svg(result: SectionResult, path: Path, *, width_px: int = 1600) -> Path:
    width, height = _pixel_size(result, width_px)
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<rect x="1" y="1" width="{width - 2}" height="{height - 2}" '
        f'fill="none" stroke="#2B3440" stroke-width="2"/>',
    ]
    colors = _layer_colors(result)
    for code, polylines in result.polylines.items():
        color = colors.get(code, "#333333")
        for line in polylines:
            points = " ".join(
                f"{_sx(u, result, width):.1f},{_sy(v, result, height):.1f}" for u, v in line
            )
            svg.append(
                f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.5"/>'
            )
    for fault_id, trace in result.fault_traces.items():
        points = " ".join(
            f"{_sx(u, result, width):.1f},{_sy(v, result, height):.1f}" for u, v in trace
        )
        svg.append(
            f'<polyline points="{points}" fill="none" stroke="#E8913A" '
            f'stroke-width="2" stroke-dasharray="6,4"/>'
        )
        svg.append(
            f'<text x="{_sx(trace[0][0], result, width):.1f}" y="{_sy(trace[0][1], result, height):.1f}" '
            f'fill="#E8913A" font-size="12">{fault_id}</text>'
        )
    title = result.plan.name
    crs = f" · EPSG:{result.plan.crs_epsg}" if result.plan.crs_epsg else ""
    svg.append(
        f'<text x="16" y="26" fill="#2B3440" font-size="14" font-weight="bold">{title}{crs}</text>'
    )
    svg.append("</svg>")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(svg), encoding="utf-8")
    logger.info("剖面 SVG 已导出: %s", path)
    return path


def export_png(result: SectionResult, path: Path, *, dpi: int = 150) -> Path:
    from geomodel3d.dataio.charts import configure_matplotlib

    plt = configure_matplotlib()
    fig, ax = plt.subplots(figsize=(12, 6), dpi=dpi)
    colors = _layer_colors(result)
    for code, polylines in result.polylines.items():
        for line in polylines:
            arr = np.asarray(line)
            ax.plot(arr[:, 0], arr[:, 1], color=colors.get(code, "#333333"), lw=1.2, label=code)
    if result.boreholes:
        for hole in result.boreholes:
            ax.axvline(hole.u, color="#999999", lw=0.6, ls="--")
            ax.text(hole.u, hole.collar_v, hole.hole_id, fontsize=7, rotation=90, va="bottom")
    for fault_id, trace in result.fault_traces.items():
        arr = np.asarray(trace)
        ax.plot(arr[:, 0], arr[:, 1], color="#E8913A", lw=1.6, ls="-.", label=f"断层{fault_id}")
        ax.text(arr[0, 0], arr[0, 1], fault_id, color="#E8913A", fontsize=8)
    for annotation in result.annotations():
        if annotation["kind"] == "fault":
            continue
        color = "#2B3440" if annotation["kind"] == "layer" else "#5A6B7C"
        size = 8 if annotation["kind"] == "layer" else 6
        ax.text(annotation["u"], annotation["v"], annotation["text"], fontsize=size, color=color)
    _add_scale_bar(ax, result)
    _add_neatline(ax, result)
    _add_title_block(ax, result)
    if result.plan.kind.value == "horizontal":
        _add_north_arrow(ax, result)
    if result.plan.crs_epsg:
        ax.text(
            0.99, 0.01, f"EPSG:{result.plan.crs_epsg}",
            transform=ax.transAxes, ha="right", va="bottom", fontsize=8, color="#5A6B7C",
        )
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("距离 u (m)")
    ax.set_ylabel("高程 v (m)")
    ax.set_title(result.plan.name)
    ax.grid(True, ls=":", alpha=0.4)
    ax.minorticks_on()
    ax.tick_params(which="minor", length=2, color="#9AA7B4")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    try:
        fig.savefig(path)
    finally:
        plt.close(fig)
    logger.info("剖面 PNG 已导出: %s", path)
    return path


def export_section(result: SectionResult, path: Path, fmt: Optional[str] = None) -> Path:
    fmt = (fmt or Path(path).suffix.lstrip(".")).lower()
    if fmt == "geojson" or fmt == "json":
        return export_geojson(result, path)
    if fmt == "shp":
        return export_shp(result, path)
    if fmt == "dxf":
        return export_dxf(result, path)
    if fmt == "svg":
        return export_svg(result, path)
    if fmt == "png":
        return export_png(result, path)
    raise GeoModelError(ErrorCode.EXP_CHK_001, f"不支持的剖面格式: {fmt}")


# --------------------------------------------------------------------------- #
# 渲染辅助
# --------------------------------------------------------------------------- #


def _pixel_size(result: SectionResult, width_px: int):
    umin, vmin, umax, vmax = result.bounds_uv
    width = max(umax - umin, 1e-6)
    height = max(vmax - vmin, 1e-6)
    scale = width_px / width
    return width_px, max(int(height * scale), 1)


def _sx(u: float, result: SectionResult, width: int) -> float:
    umin, _, umax, _ = result.bounds_uv
    return (u - umin) / max(umax - umin, 1e-6) * width


def _sy(v: float, result: SectionResult, height: int) -> float:
    _, vmin, _, vmax = result.bounds_uv
    return height - (v - vmin) / max(vmax - vmin, 1e-6) * height


def _layer_colors(result: SectionResult) -> Dict[str, str]:
    palette = ["#7FA8C9", "#D9CBB8", "#6B7A8D", "#E8913A", "#2F9E63", "#9B7FB0"]
    return {
        code: palette[index % len(palette)]
        for index, code in enumerate(sorted(result.polylines))
    }


def _fault_world(result: SectionResult, trace: List[List[float]]) -> List[List[float]]:
    origin = np.asarray(result.plan.origin, dtype=float)
    u_axis = np.asarray(result.plan.u_axis, dtype=float)
    v_axis = np.asarray(result.plan.v_axis, dtype=float)
    return [(origin + u * u_axis + v * v_axis).tolist() for u, v in trace]


def _add_scale_bar(ax, result: SectionResult) -> None:
    import math

    umin, vmin, umax, vmax = result.bounds_uv
    span = max(umax - umin, 1e-6)
    raw = span / 5.0
    exponent = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1.0
    length = exponent
    for multiplier in (1, 2, 5, 10):
        if multiplier * exponent >= raw:
            length = multiplier * exponent
            break
    x0 = umin + span * 0.05
    y0 = vmin + max(vmax - vmin, 1e-6) * 0.06
    ax.plot([x0, x0 + length], [y0, y0], color="#2B3440", lw=2)
    ax.plot([x0, x0], [y0 - 0.01 * (vmax - vmin), y0 + 0.01 * (vmax - vmin)], color="#2B3440", lw=1)
    ax.plot([x0 + length, x0 + length], [y0 - 0.01 * (vmax - vmin), y0 + 0.01 * (vmax - vmin)], color="#2B3440", lw=1)
    ax.text(x0 + length / 2.0, y0, f"{length:g} m", ha="center", va="bottom", fontsize=8)


def _add_neatline(ax, result: SectionResult) -> None:
    from matplotlib.patches import Rectangle

    umin, vmin, umax, vmax = result.bounds_uv
    pad = 0.02 * max(umax - umin, vmax - vmin, 1.0)
    ax.add_patch(
        Rectangle(
            (umin - pad, vmin - pad),
            (umax - umin) + 2 * pad,
            (vmax - vmin) + 2 * pad,
            fill=False, edgecolor="#2B3440", lw=1.2,
        )
    )


def _add_title_block(ax, result: SectionResult) -> None:
    from geomodel3d.algorithms.section import section_azimuth

    lines = [result.plan.name]
    if result.plan.crs_epsg:
        lines.append(f"CRS: EPSG:{result.plan.crs_epsg}")
    lines.append(f"方位: {section_azimuth(result.plan):.0f}°")
    if result.plan.scale:
        lines.append(f"比例尺 1:{int(result.plan.scale)}")
    lines.append(f"生成: {result.created[:10]}")
    ax.text(
        0.01, 0.99, "\n".join(lines),
        transform=ax.transAxes, va="top", ha="left", fontsize=8,
        bbox=dict(boxstyle="round", fc="white", ec="#C9D2DC", alpha=0.9),
    )


def _add_north_arrow(ax, result: SectionResult) -> None:
    ax.annotate(
        "N", xy=(0.97, 0.92), xytext=(0.97, 0.82),
        xycoords="axes fraction", textcoords="axes fraction",
        ha="center", va="bottom", fontsize=10, color="#2B3440",
        arrowprops=dict(arrowstyle="-|>", color="#2B3440", lw=1.2),
    )


__all__ = [
    "export_mesh",
    "export_section",
    "export_geojson",
    "export_shp",
    "export_dxf",
    "export_svg",
    "export_png",
    "layer_name",
]
