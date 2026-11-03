"""坐标系服务（8.5）。

- CRS 描述统一为 :class:`~geomodel3d.config.schema.CRSConfig`；
- 提供"源→目标"变换链，保证导入可逆、导出可追溯；
- 内部计算使用工程局部坐标（相对工程原点），导出前还原绝对坐标。
"""
from __future__ import annotations

import logging
from typing import List, Optional, Sequence, Tuple

import numpy as np

from geomodel3d.config.errors import ConfigurationError
from geomodel3d.config.schema import CRSConfig

logger = logging.getLogger(__name__)


def pyproj_available() -> bool:
    try:
        import pyproj  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _to_crs(config: CRSConfig):
    import pyproj

    if config.wkt:
        return pyproj.CRS.from_wkt(config.wkt)
    if config.epsg:
        return pyproj.CRS.from_epsg(config.epsg)
    if config.proj4:
        return pyproj.CRS.from_proj4(config.proj4)
    raise ConfigurationError("CRS 描述为空，无法构建坐标参考系。")


class CRSService:
    """坐标变换与工程局部坐标管理。"""

    def __init__(self, origin: Optional[Sequence[float]] = None) -> None:
        self.origin = np.asarray(origin if origin is not None else [0.0, 0.0, 0.0], dtype=np.float64)
        self._chain: List[Tuple[Optional[int], Optional[int]]] = []

    @property
    def transform_chain(self) -> List[Tuple[Optional[int], Optional[int]]]:
        return list(self._chain)

    def transformer(self, source: CRSConfig, target: CRSConfig, *, always_xy: bool = True):
        import pyproj

        src = _to_crs(source)
        dst = _to_crs(target)
        return pyproj.Transformer.from_crs(src, dst, always_xy=always_xy)

    def transform_points(
        self,
        points: np.ndarray,
        source: CRSConfig,
        target: CRSConfig,
    ) -> np.ndarray:
        """变换 ``(N, 3)`` 点（含 Z；无垂直基准时 Z 原样保留）。"""
        pts = np.asarray(points, dtype=np.float64)
        transformer = self.transformer(source, target)
        x, y, z = transformer.transform(pts[:, 0], pts[:, 1], pts[:, 2])
        self._chain.append((source.epsg, target.epsg))
        if len(self._chain) > 100:
            del self._chain[: len(self._chain) - 100]
        out = pts.copy()
        out[:, 0] = x
        out[:, 1] = y
        out[:, 2] = z
        return out

    # 工程局部坐标 ----------------------------------------------------- #
    def set_origin(self, origin: Sequence[float]) -> None:
        self.origin = np.asarray(origin, dtype=np.float64)

    def to_local(self, points: np.ndarray) -> np.ndarray:
        return np.asarray(points, dtype=np.float64) - self.origin

    def to_world(self, points: np.ndarray) -> np.ndarray:
        return np.asarray(points, dtype=np.float64) + self.origin


def validate_crs(config: CRSConfig) -> bool:
    """校验 CRS 是否可解析（BR-06 防呆：无 CRS 不允许导出）。"""
    if not (config.epsg or config.proj4 or config.wkt):
        return False
    if not pyproj_available():
        return True
    try:
        _to_crs(config)
        return True
    except ConfigurationError:
        return False
    except Exception:  # noqa: BLE001 - pyproj 异常统一视为不可用 CRS
        logger.warning("CRS 解析失败: %s", config.model_dump(), exc_info=True)
        return False


__all__ = ["CRSService", "validate_crs", "pyproj_available"]
