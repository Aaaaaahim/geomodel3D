"""钻孔数据解析（M1-F1 / 8.2）。

支持 CSV/Excel，提供列名模糊匹配与手工映射，输出标准化
:class:`~geomodel3d.domain.entities.Drillhole` 列表。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from geomodel3d.config.errors import GeoModelError, ErrorCode
from geomodel3d.domain.entities import Drillhole, DrillholeInterval

# 标准列名 -> 同义列（模糊匹配）
TEMPLATE: Dict[str, List[str]] = {
    "hole_id": ["hole_id", "holeid", "孔号", "钻孔号", "孔号id", "钻孔编号"],
    "x": ["x", "东坐标", "easting", "x坐标", "孔口x"],
    "y": ["y", "北坐标", "northing", "y坐标", "孔口y"],
    "collar_elev": ["collar_elev", "z_surface", "孔口高程", "孔口标高", "elev", "高程", "z"],
    "layer_top": ["layer_top", "层顶深", "顶深", "top", "起始深度"],
    "layer_base": ["layer_base", "层底深", "底深", "bottom", "base", "终止深度"],
    "layer_code": [
        "layer_code", "formation_name", "地层代码", "地层名称", "层号", "地层",
        "岩性代码", "岩性名称", "岩性", "code",
    ],
    "thickness": ["thickness", "厚度", "层厚"],
    "grade": ["grade", "品位", "品位值", "grade_value"],
    "grade_unit": ["grade_unit", "品位单位", "单位"],
}

# 以"标高"（绝对高程）而非"深度"给出的层顶/底列（如参考项目样例数据）
_ELEV_TOP_ALIASES = ["layer_top_elev", "top_elev", "top_elevation", "topsurface", "层顶高程", "顶板标高"]
_ELEV_BASE_ALIASES = ["layer_base_elev", "bottom_elev", "bottom_elevation", "bottomsurface", "层底高程", "底板标高"]

REQUIRED = ["hole_id", "x", "y", "collar_elev", "layer_code"]


def _normalize(name: str) -> str:
    return str(name).strip().lower().replace(" ", "").replace("_", "")


def _first_exact(normalized: Dict[str, str], aliases: List[str]) -> Optional[str]:
    for alias in aliases:
        key = _normalize(alias)
        if key in normalized:
            return normalized[key]
    return None


def detect_mapping(columns: List[str]) -> Dict[str, Optional[str]]:
    """把文件列名映射到标准列；未识别的标准列值为 ``None``。"""
    normalized = {_normalize(col): col for col in columns}
    # 先识别"标高"列并移出，避免被"层顶深/层底深"的模糊别名（top/bottom）误吞
    elev_top = _first_exact(normalized, _ELEV_TOP_ALIASES)
    elev_base = _first_exact(normalized, _ELEV_BASE_ALIASES)
    for column in (elev_top, elev_base):
        if column:
            normalized.pop(_normalize(column), None)

    mapping: Dict[str, Optional[str]] = {}
    for standard, aliases in TEMPLATE.items():
        found = None
        for alias in aliases:
            key = _normalize(alias)
            if key in normalized:
                found = normalized[key]
                break
        if found is None:
            # 仅用长度≥2 的别名做子串模糊匹配，避免 "x" 命中 "max" 之类误判
            for key, original in normalized.items():
                if any(len(a2) >= 2 and a2 in key for a2 in (_normalize(a) for a in aliases)):
                    found = original
                    break
        mapping[standard] = found
    mapping["layer_top_elev"] = elev_top
    mapping["layer_base_elev"] = elev_base
    return mapping


def read_table(path: Path, *, sheet: Optional[str] = None, header: Optional[str] = "infer") -> pd.DataFrame:
    """读取 CSV/Excel，自动探测 CSV 编码（UTF-8/GBK）。``header=None`` 用于无表头文件。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        excel_kwargs = {} if header == "infer" else {"header": header}
        return pd.read_excel(path, sheet_name=sheet or 0, **excel_kwargs)
    for encoding in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return pd.read_csv(path, encoding=encoding, header=header)
        except UnicodeDecodeError:
            continue
    raise GeoModelError(
        ErrorCode.DAT_PAR_001, f"无法识别文件编码: {path.name}", context={"path": str(path)}
    )


def read_boreholes(
    path: Path,
    *,
    mapping: Optional[Dict[str, Optional[str]]] = None,
    sheet: Optional[str] = None,
) -> List[Drillhole]:
    """解析钻孔表为领域对象列表。"""
    frame = read_table(path, sheet=sheet)
    resolved = detect_mapping(list(frame.columns))
    if mapping:
        resolved.update({k: v for k, v in mapping.items() if v})

    missing = [col for col in REQUIRED if not resolved.get(col)]
    has_depth = bool(resolved.get("layer_top") and resolved.get("layer_base"))
    has_elev = bool(resolved.get("layer_top_elev") and resolved.get("layer_base_elev"))
    if not has_depth and not has_elev:
        missing.append("层顶深+层底深 或 顶板标高+底板标高")
    if missing:
        raise GeoModelError(
            ErrorCode.DAT_PAR_001,
            f"缺少必填列: {', '.join(missing)}",
            context={"columns": list(frame.columns), "missing": missing},
        )

    holes: Dict[str, Drillhole] = {}
    raw_rows: Dict[str, List[dict]] = {}
    for _, row in frame.iterrows():
        hole_id = str(row[resolved["hole_id"]]).strip()
        if not hole_id or hole_id.lower() == "nan":
            continue
        collar = float(row[resolved["collar_elev"]])
        if hole_id not in holes:
            holes[hole_id] = Drillhole(
                hole_id=hole_id,
                x=float(row[resolved["x"]]),
                y=float(row[resolved["y"]]),
                collar_elev=collar,
            )
            raw_rows[hole_id] = []

        if has_depth:
            depth_top = float(row[resolved["layer_top"]])
            depth_bottom = float(row[resolved["layer_base"]])
        else:
            depth_top = _clamp_depth(collar - float(row[resolved["layer_top_elev"]]))
            depth_bottom = _clamp_depth(collar - float(row[resolved["layer_base_elev"]]))
        raw_rows[hole_id].append(
            {
                "code": str(row[resolved["layer_code"]]).strip(),
                "top": depth_top,
                "bottom": depth_bottom,
                "grade": _opt_float(row, resolved.get("grade")),
                "unit": str(row[resolved["grade_unit"]]) if resolved.get("grade_unit") else "%",
            }
        )

    for hole_id, rows in raw_rows.items():
        hole = holes[hole_id]
        rows.sort(key=lambda item: item["bottom"])
        previous_bottom = 0.0
        for item in rows:
            depth_top, depth_bottom = item["top"], item["bottom"]
            if depth_bottom < depth_top:
                depth_top, depth_bottom = depth_bottom, depth_top
            # 同一孔内按层底深整理为连续层序（兼容"累计厚度"式数据）
            if depth_top < previous_bottom - 1e-6:
                depth_top = previous_bottom
            if depth_bottom < depth_top:
                depth_bottom = depth_top
            interval = DrillholeInterval(
                layer_code=item["code"],
                depth_top=depth_top,
                depth_bottom=depth_bottom,
                grade=item["grade"],
                grade_unit=item["unit"],
            )
            hole.intervals.append(interval)
            hole.total_depth = max(hole.total_depth, depth_bottom)
            previous_bottom = depth_bottom
    return list(holes.values())


def _clamp_depth(value: float, tolerance: float = 0.05) -> float:
    """把标高换算产生的微小负深度（舍入误差）归零；较大负值保留以暴露数据错误。"""
    if -tolerance <= value < 0.0:
        return 0.0
    return value


def _opt_float(row: pd.Series, column: Optional[str]) -> Optional[float]:
    if not column:
        return None
    value = row.get(column)
    if value is None or pd.isna(value):
        return None
    return float(value)


__all__ = ["TEMPLATE", "REQUIRED", "detect_mapping", "read_table", "read_boreholes"]
