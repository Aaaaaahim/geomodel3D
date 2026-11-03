"""地震数据解析（M1-F4 / 8.2）。

支持 Esri ASCII 栅格（``.asc``/``.grd``）与 SEG-Y 地震道集（``.sgy``/``.segy``），
归一化为 :class:`~geomodel3d.domain.entities.SeismicLayer` 点云/趋势面。
纯 Python 实现，不依赖 GDAL/segyio。
"""
from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import Dict, Optional

import numpy as np

from geomodel3d.config.errors import ErrorCode, GeoModelError
from geomodel3d.domain.entities import SeismicLayer

logger = logging.getLogger(__name__)

TEXTUAL_HEADER = 3200
BINARY_HEADER = 400
TRACE_HEADER = 240
MAX_POINTS = 200_000


def _detect_source_crs(points: np.ndarray) -> Optional[int]:
    """按地震点云平面坐标启发式推断源 CRS（无法判定时返回 None）。"""
    if points.size == 0:
        return None
    try:
        from geomodel3d.dataio.crs_detect import detect_crs

        suggestion = detect_crs(np.asarray(points)[:, :2])[0]
    except Exception:  # noqa: BLE001
        logger.debug("地震源 CRS 自动识别失败", exc_info=True)
        return None
    return suggestion.epsg if suggestion.usable else None


# --------------------------------------------------------------------------- #
# Esri ASCII 栅格
# --------------------------------------------------------------------------- #


def read_esri_ascii_grid(
    path: Path,
    *,
    max_points: int = MAX_POINTS,
    source_crs_epsg: Optional[int] = None,
) -> SeismicLayer:
    """读取 Esri ASCII 栅格为趋势面点云。"""
    path = Path(path)
    header: Dict[str, float] = {}
    values = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            parts = line.split()
            if not parts:
                continue
            key = parts[0].lower()
            if key in ("ncols", "nrows", "xllcorner", "yllcorner", "xllcenter", "yllcenter",
                       "cellsize", "nodata_value"):
                header[key] = float(parts[1])
            else:
                values.append([float(v) for v in parts])
    required = ("ncols", "nrows", "cellsize")
    if any(key not in header for key in required):
        raise GeoModelError(ErrorCode.DAT_PAR_001, "Esri ASCII 栅格缺少必要头信息。")

    ncols = int(header["ncols"])
    nrows = int(header["nrows"])
    cellsize = header["cellsize"]
    x0 = header.get("xllcorner", header.get("xllcenter", 0.0))
    y0 = header.get("yllcorner", header.get("yllcenter", 0.0))
    nodata = header.get("nodata_value", -9999.0)
    grid = np.asarray(values, dtype=np.float64)
    if grid.shape != (nrows, ncols):
        raise GeoModelError(
            ErrorCode.DAT_VAL_001,
            f"栅格尺寸不匹配: 期望 {nrows}x{ncols}, 实际 {grid.shape}",
        )

    step = max(1, int(np.ceil(np.sqrt(nrows * ncols / max_points))))
    xs = x0 + (np.arange(0, ncols, step) + 0.5) * cellsize
    ys = y0 + (np.arange(nrows - 1, -1, -step) + 0.5) * cellsize
    xx, yy = np.meshgrid(xs, ys)
    zz = grid[np.ix_(np.arange(0, nrows, step), np.arange(0, ncols, step))]
    valid = zz != nodata
    points = np.column_stack([xx[valid], yy[valid], zz[valid]])
    logger.info("读取 Esri ASCII 栅格 %s: %d 点", path.name, points.shape[0])
    return SeismicLayer(
        name=path.stem,
        kind="grid",
        points=points.tolist(),
        smooth=1.0,
        source_crs_epsg=source_crs_epsg or _detect_source_crs(points),
    )


# --------------------------------------------------------------------------- #
# SEG-Y（最小实现：IEEE/IBM float）
# --------------------------------------------------------------------------- #


def read_segy(
    path: Path,
    *,
    time_window: Optional[tuple] = None,
    max_traces: int = 20000,
    source_crs_epsg: Optional[int] = None,
) -> SeismicLayer:
    """读取 SEG-Y，按道抽取时间窗内最大振幅样点，形成趋势面点云。"""
    path = Path(path)
    if path.stat().st_size < TEXTUAL_HEADER + BINARY_HEADER:
        raise GeoModelError(ErrorCode.DAT_PAR_001, "SEG-Y 文件过小。")

    xs, ys, zs = [], [], []
    with path.open("rb") as handle:
        handle.seek(TEXTUAL_HEADER)
        binary = handle.read(BINARY_HEADER)
        sample_interval = _u16(binary, 16)      # 字节 3217-3218
        samples_per_trace = _u16(binary, 20)    # 字节 3221-3222
        format_code = _u16(binary, 24)          # 字节 3225-3226
        if samples_per_trace <= 0:
            raise GeoModelError(ErrorCode.DAT_PAR_001, "SEG-Y 道样点数无效。")
        bytes_per_sample = _bytes_per_sample(format_code)
        trace_size = TRACE_HEADER + samples_per_trace * bytes_per_sample

        trace_index = 0
        while trace_index < max_traces:
            header = handle.read(TRACE_HEADER)
            if len(header) < TRACE_HEADER:
                break
            payload = handle.read(samples_per_trace * bytes_per_sample)
            if len(payload) < samples_per_trace * bytes_per_sample:
                break
            scalar = _coord_scalar(_i16(header, 70))
            x = _i32(header, 180) * scalar
            y = _i32(header, 184) * scalar
            samples = _decode_samples(payload, format_code, samples_per_trace)
            if samples.size:
                times = np.arange(samples.size) * (sample_interval / 1_000_000.0)
                if time_window is not None:
                    lo, hi = time_window
                    mask = (times >= lo) & (times <= hi)
                    if mask.any():
                        window_samples = np.abs(samples[mask])
                        pick = int(window_samples.argmax())
                        zs.append(float(times[mask][pick]))
                        xs.append(float(x))
                        ys.append(float(y))
                else:
                    zs.append(float(times[int(np.abs(samples).argmax())]))
                    xs.append(float(x))
                    ys.append(float(y))
            trace_index += 1

    if not xs:
        raise GeoModelError(ErrorCode.DAT_VAL_001, "SEG-Y 未解析到有效道。")
    points = np.column_stack([xs, ys, zs])
    logger.info("读取 SEG-Y %s: %d 道, 格式码 %d", path.name, len(xs), format_code)
    return SeismicLayer(
        name=path.stem,
        kind="horizon",
        points=points.tolist(),
        smooth=1.0,
        source_crs_epsg=source_crs_epsg or _detect_source_crs(points),
    )


def _bytes_per_sample(format_code: int) -> int:
    if format_code == 1:
        return 4
    if format_code == 2:
        return 4
    if format_code == 3:
        return 2
    if format_code == 5:
        return 4
    if format_code == 8:
        return 1
    raise GeoModelError(ErrorCode.DAT_PAR_001, f"不支持的 SEG-Y 采样格式码: {format_code}")


def _decode_samples(payload: bytes, format_code: int, count: int) -> np.ndarray:
    if format_code == 5:
        return np.frombuffer(payload[: count * 4], dtype=">f4").astype(np.float64)
    if format_code == 1:
        raw = np.frombuffer(payload[: count * 4], dtype=">u4")
        return _ibm_to_float(raw)
    if format_code == 2:
        return np.frombuffer(payload[: count * 4], dtype=">i4").astype(np.float64)
    if format_code == 3:
        return np.frombuffer(payload[: count * 2], dtype=">i2").astype(np.float64)
    if format_code == 8:
        return np.frombuffer(payload[:count], dtype="i1").astype(np.float64)
    return np.empty(0)


def _ibm_to_float(raw: np.ndarray) -> np.ndarray:
    """IBM 单精度浮点（格式码 1）转 IEEE float。"""
    sign = np.where((raw >> 31) & 1, -1.0, 1.0)
    exponent = ((raw >> 24) & 0x7F).astype(np.int64) - 64
    mantissa = (raw & 0x00FFFFFF).astype(np.float64)
    with np.errstate(over="ignore"):
        values = sign * mantissa * np.power(16.0, exponent) / (2**24)
    return values


def _u16(buf: bytes, offset: int) -> int:
    return struct.unpack_from(">H", buf, offset)[0]


def _i16(buf: bytes, offset: int) -> int:
    return struct.unpack_from(">h", buf, offset)[0]


def _i32(buf: bytes, offset: int) -> int:
    return struct.unpack_from(">i", buf, offset)[0]


def _coord_scalar(value: int) -> float:
    if value > 0:
        return float(value)
    if value < 0:
        return 1.0 / abs(value)
    return 1.0


def read_seismic(path: Path, *, source_crs_epsg: Optional[int] = None) -> SeismicLayer:
    """按扩展名/内容分派地震数据解析。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".asc", ".grd"):
        return read_esri_ascii_grid(path, source_crs_epsg=source_crs_epsg)
    if suffix in (".txt", ".csv"):
        # .txt/.csv 也可能是 Esri ASCII 栅格，按内容嗅探
        if _looks_like_esri_ascii(path):
            return read_esri_ascii_grid(path, source_crs_epsg=source_crs_epsg)
        return _read_point_cloud(path, source_crs_epsg=source_crs_epsg)
    if suffix in (".sgy", ".segy"):
        return read_segy(path, source_crs_epsg=source_crs_epsg)
    raise GeoModelError(ErrorCode.DAT_PAR_001, f"不支持的地震数据格式: {suffix}")


_ESRI_KEYS = {"ncols", "nrows", "xllcorner", "yllcorner", "xllcenter", "yllcenter", "cellsize", "nodata_value"}


def _looks_like_esri_ascii(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for _ in range(5):
                line = handle.readline()
                if not line:
                    return False
                parts = line.split()
                if parts and parts[0].lower() in _ESRI_KEYS:
                    return True
    except OSError:
        return False
    return False


def read_seismic_multi(paths, *, source_crs_epsg: Optional[int] = None) -> SeismicLayer:
    """读取多个地震文件并合并为单一趋势面（支持 SEG-Y 多卷/多线）。"""
    if isinstance(paths, (str, Path)):
        paths = [paths]
    files = [Path(p) for p in paths]
    layers = [read_seismic(path, source_crs_epsg=source_crs_epsg) for path in files]
    if len(layers) == 1:
        return layers[0]
    arrays = [np.asarray(layer.points, dtype=np.float64) for layer in layers if layer.points]
    if not arrays:
        raise GeoModelError(ErrorCode.DAT_VAL_001, "所选地震文件均无有效点。")
    points = np.vstack(arrays)
    smooth = max((layer.smooth for layer in layers), default=0.0)
    epsgs = {layer.source_crs_epsg for layer in layers if layer.source_crs_epsg is not None}
    merged_epsg = epsgs.pop() if len(epsgs) == 1 else None
    if len(epsgs) > 1:
        logger.warning("合并的地震文件源 CRS 不一致 %s，已置空待人工指定。", epsgs)
    logger.info("合并 %d 个地震文件: %d 点", len(files), points.shape[0])
    return SeismicLayer(
        name=f"{files[0].stem}_multi",
        kind="merged",
        points=points.tolist(),
        smooth=smooth,
        source_crs_epsg=merged_epsg,
    )


_X_ALIASES = ["x", "easting", "east", "东坐标", "x坐标", "横坐标", "经度", "lon", "long", "longitude", "cdp_x", "cdpx"]
_Y_ALIASES = ["y", "northing", "north", "北坐标", "y坐标", "纵坐标", "纬度", "lat", "latitude", "cdp_y", "cdpy"]
_Z_ALIASES = [
    "z", "elev", "elevation", "高程", "标高", "海拔", "深度", "depth",
    "twt", "time", "traveltime", "amplitude", "amp", "horizon", "value",
]


def _norm(name: str) -> str:
    return str(name).strip().lower().replace(" ", "").replace("_", "").replace("-", "")


def _match_column(normalized: Dict[str, str], aliases: List[str]) -> Optional[str]:
    for alias in aliases:
        key = _norm(alias)
        if key in normalized:
            return normalized[key]
    for key, original in normalized.items():
        if any(len(_norm(a)) >= 2 and _norm(a) in key for a in aliases):
            return original
    return None


def _read_point_cloud(path: Path, *, source_crs_epsg: Optional[int] = None) -> SeismicLayer:
    from geomodel3d.dataio.borehole_io import read_table

    frame = read_table(path)
    # 若无表头（列名本身就是数字），重新按无表头读取
    if _all_numeric_names(frame.columns):
        frame = read_table(path, header=None)

    normalized = {_norm(c): c for c in frame.columns}
    x_col = _match_column(normalized, _X_ALIASES)
    y_col = _match_column(normalized, _Y_ALIASES)
    z_col = _match_column(normalized, _Z_ALIASES)

    if x_col is None or y_col is None or z_col is None:
        # 无法识别列名时，若前三列均为数值则按 x,y,z 处理
        first_three = list(frame.columns[:3])
        if len(first_three) == 3 and all(_is_numeric(frame[column]) for column in first_three):
            logger.warning("地震点云列名无法识别，按前三列 x,y,z 处理: %s", first_three)
            x_col, y_col, z_col = first_three
        else:
            raise GeoModelError(
                ErrorCode.DAT_PAR_001,
                "地震点云需包含 x/y/z 列（或东坐标/北坐标/高程）。"
                f" 当前列: {list(frame.columns)}",
                context={"columns": [str(c) for c in frame.columns]},
            )

    points = frame[[x_col, y_col, z_col]].to_numpy(dtype=float)
    points = points[np.isfinite(points).all(axis=1)]
    if points.shape[0] == 0:
        raise GeoModelError(ErrorCode.DAT_VAL_001, "地震点云无有效数据行。")
    return SeismicLayer(
        name=path.stem,
        kind="point_cloud",
        points=points.tolist(),
        source_crs_epsg=source_crs_epsg or _detect_source_crs(points),
    )


def _all_numeric_names(columns) -> bool:
    """判断列名是否全为数字（说明文件无表头）。"""
    names = [str(c) for c in columns]
    if not names:
        return False
    for name in names:
        try:
            float(name)
        except ValueError:
            return False
    return True


def _is_numeric(series) -> bool:
    try:
        return bool(np.issubdtype(series.dropna().to_numpy().dtype, np.number))
    except Exception:  # noqa: BLE001
        return False


__all__ = ["read_esri_ascii_grid", "read_segy", "read_seismic", "read_seismic_multi"]
