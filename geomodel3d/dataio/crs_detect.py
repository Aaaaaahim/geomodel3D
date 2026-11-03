"""坐标系自动识别（M1-F5 / 8.5）。

根据坐标数值的**量级/范围/带号前缀**给出 EPSG 候选与置信度，
供导入向导预选并在缺失时自动补充工程 CRS。识别为启发式，用户可覆盖。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

import numpy as np

# 中国陆地大致经纬度范围
_CHINA_LON = (73.0, 136.0)
_CHINA_LAT = (3.0, 54.0)


@dataclass
class CRSSuggestion:
    epsg: Optional[int]
    name: str
    confidence: str  # high / medium / low
    reason: str

    @property
    def usable(self) -> bool:
        """是否可用于自动补充（high/medium 且已知 EPSG）。"""
        return self.epsg is not None and self.confidence in ("high", "medium")


def detect_crs(points: Sequence[Sequence[float]]) -> List[CRSSuggestion]:
    """根据点坐标给出 EPSG 候选（按优先级排序）。"""
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[0] == 0 or pts.shape[1] < 2:
        return [CRSSuggestion(None, "无有效坐标", "low", "样本为空")]
    pts = pts[np.isfinite(pts[:, :2]).all(axis=1)]
    if pts.shape[0] == 0:
        return [CRSSuggestion(None, "无有效坐标", "low", "坐标均为空")]

    x, y = pts[:, 0], pts[:, 1]
    xmin, xmax, ymin, ymax = x.min(), x.max(), y.min(), y.max()

    # 1) 地理坐标（经纬度）
    if -180.0 <= xmin and xmax <= 180.0 and -90.0 <= ymin and ymax <= 90.0:
        in_china = (
            _CHINA_LON[0] <= xmin and xmax <= _CHINA_LON[1]
            and _CHINA_LAT[0] <= ymin and ymax <= _CHINA_LAT[1]
        )
        if in_china:
            return [
                CRSSuggestion(4490, "CGCS2000 地理坐标（经纬度）", "high", "经纬度量级且落在国境内"),
                CRSSuggestion(4326, "WGS 84 地理坐标（经纬度）", "medium", "经纬度量级"),
            ]
        return [CRSSuggestion(4326, "WGS 84 地理坐标（经纬度）", "high", "经纬度量级")]

    # 2) 投影坐标且东坐标含带号前缀（8 位数）
    if xmin >= 10_000_000.0:
        zone = int(xmin // 1_000_000.0)
        if 25 <= zone <= 45:
            return [
                CRSSuggestion(
                    4513 + (zone - 25),
                    f"CGCS2000 3°带 第{zone}带（中央经线 {zone * 3}°E）",
                    "medium",
                    "东坐标含 3°带带号前缀",
                )
            ]
        if 13 <= zone <= 23:
            return [
                CRSSuggestion(
                    4491 + (zone - 13),
                    f"CGCS2000 6°带 第{zone}带（中央经线 {zone * 6 - 3}°E）",
                    "medium",
                    "东坐标含 6°带带号前缀",
                )
            ]
        return [CRSSuggestion(None, "投影坐标（带号越界）", "low", "东坐标带号不在常见范围")]

    # 3) 6~7 位东坐标、北坐标在百万级 → 疑 UTM 或 Gauss-Krüger（无带号）
    if 100_000.0 <= xmin and xmax <= 999_999.0 and 0.0 <= ymin and ymax <= 10_000_000.0:
        return [
            CRSSuggestion(
                None,
                "投影坐标（疑 UTM 或 CGCS2000 高斯投影），需指定带号",
                "low",
                "东坐标 6~7 位且无带号前缀，无法判定带号",
            )
        ]

    return [CRSSuggestion(None, "无法自动判定坐标系", "low", "坐标量级不常见")]


def best_epsg(points: Sequence[Sequence[float]], *, allow_medium: bool = True) -> Optional[int]:
    """返回最可信且可用的 EPSG（无则 ``None``）。"""
    for suggestion in detect_crs(points):
        if suggestion.epsg is None:
            continue
        if suggestion.confidence == "high" or (allow_medium and suggestion.confidence == "medium"):
            return suggestion.epsg
    return None


__all__ = ["CRSSuggestion", "detect_crs", "best_epsg"]
