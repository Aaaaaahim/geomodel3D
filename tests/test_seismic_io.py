"""地震数据解析测试（M1-F4）：Esri ASCII 栅格与 SEG-Y。"""
from __future__ import annotations

import struct

import numpy as np
import pytest

from geomodel3d.config.errors import GeoModelError
from geomodel3d.dataio.seismic_io import read_esri_ascii_grid, read_segy, read_seismic


def test_read_esri_ascii_grid(tmp_path):
    path = tmp_path / "surface.asc"
    path.write_text(
        "ncols 3\nnrows 2\nxllcorner 100\nyllcorner 200\ncellsize 10\nNODATA_value -9999\n"
        "1 2 3\n4 -9999 6\n",
        encoding="utf-8",
    )
    layer = read_esri_ascii_grid(path)
    assert layer.kind == "grid"
    assert len(layer.points) == 5  # 6 格 - 1 NODATA
    xs = [p[0] for p in layer.points]
    ys = [p[1] for p in layer.points]
    assert min(xs) == pytest.approx(105.0)
    assert max(ys) == pytest.approx(215.0)


def _write_segy(path, traces):
    """构造最小 SEG-Y（IEEE float，格式码 5）。"""
    samples_per_trace = len(traces[0][2])
    textual = b"C" * 3200
    binary = bytearray(400)
    struct.pack_into(">H", binary, 16, 2000)  # 采样间隔 2000us
    struct.pack_into(">H", binary, 20, samples_per_trace)
    struct.pack_into(">H", binary, 24, 5)  # IEEE float
    payload = bytearray(textual) + bytearray(binary)
    for x, y, samples in traces:
        header = bytearray(240)
        struct.pack_into(">i", header, 180, int(x))
        struct.pack_into(">i", header, 184, int(y))
        struct.pack_into(">h", header, 70, 1)
        payload += header
        payload += struct.pack(f">{samples_per_trace}f", *samples)
    path.write_bytes(bytes(payload))


def test_read_segy_ieee(tmp_path):
    path = tmp_path / "line.sgy"
    _write_segy(
        path,
        [
            (100, 200, [0.1, 0.2, 9.9, 0.3]),
            (110, 200, [0.1, 8.8, 0.2, 0.3]),
        ],
    )
    layer = read_segy(path)
    assert layer.kind == "horizon"
    assert len(layer.points) == 2
    # 第一道最大振幅在样点 2 -> 时间 2*2000us = 0.004s
    assert layer.points[0][2] == pytest.approx(0.004)
    assert layer.points[0][0] == pytest.approx(100.0)


def test_read_seismic_dispatch(tmp_path):
    path = tmp_path / "cloud.csv"
    path.write_text("x,y,z\n0,0,10\n1,1,11\n", encoding="utf-8")
    layer = read_seismic(path)
    assert layer.kind == "point_cloud"
    assert len(layer.points) == 2


def test_read_point_cloud_chinese_gbk(tmp_path):
    path = tmp_path / "cloud.csv"
    path.write_text("东坐标,北坐标,高程\n0,0,10\n1,1,11\n", encoding="gbk")
    layer = read_seismic(path)
    assert layer.kind == "point_cloud"
    assert layer.points[0] == [0.0, 0.0, 10.0]


def test_read_point_cloud_headerless(tmp_path):
    path = tmp_path / "cloud.txt"
    path.write_text("1.0,2.0,3.0\n4.0,5.0,6.0\n", encoding="utf-8")
    layer = read_seismic(path)
    assert len(layer.points) == 2


def test_read_point_cloud_alias_lonlatdepth(tmp_path):
    path = tmp_path / "cloud.csv"
    path.write_text("lon,lat,depth\n116.3,39.9,1200\n116.4,39.8,1250\n", encoding="utf-8")
    layer = read_seismic(path)
    assert layer.points[0] == [116.3, 39.9, 1200.0]


def test_read_point_cloud_missing_xyz_reports_columns(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("station,value1,value2\nS1,1,2\n", encoding="utf-8")
    with pytest.raises(GeoModelError) as excinfo:
        read_seismic(path)
    assert "station" in str(excinfo.value)


def test_read_esri_ascii_txt_by_sniff(tmp_path):
    path = tmp_path / "grid.txt"
    path.write_text(
        "ncols 2\nnrows 2\nxllcorner 0\nyllcorner 0\ncellsize 10\nNODATA_value -9999\n1 2\n3 4\n",
        encoding="utf-8",
    )
    layer = read_seismic(path)
    assert layer.kind == "grid"
    assert len(layer.points) == 4


def test_read_seismic_unsupported(tmp_path):
    from geomodel3d.config.errors import GeoModelError

    path = tmp_path / "x.xyz"
    path.write_text("a", encoding="utf-8")
    with pytest.raises(GeoModelError):
        read_seismic(path)


def test_read_seismic_multi_merges(tmp_path):
    from geomodel3d.dataio.seismic_io import read_seismic_multi

    first = tmp_path / "line1.sgy"
    second = tmp_path / "line2.sgy"
    _write_segy(first, [(100, 200, [0.1, 9.9, 0.2])])
    _write_segy(second, [(110, 210, [8.8, 0.2, 0.3])])
    layer = read_seismic_multi([first, second])
    assert len(layer.points) == 2
    assert layer.kind == "merged"
    assert layer.name.endswith("_multi")


def test_source_crs_autodetected_lonlat(tmp_path):
    path = tmp_path / "cloud.csv"
    path.write_text("lon,lat,depth\n116.3,39.9,1200\n116.4,39.8,1250\n", encoding="utf-8")
    layer = read_seismic(path)
    assert layer.source_crs_epsg in (4490, 4326)


def test_source_crs_explicit_overrides_detection(tmp_path):
    path = tmp_path / "grid.asc"
    path.write_text(
        "ncols 2\nnrows 2\nxllcorner 500000\nyllcorner 3000000\ncellsize 10\n"
        "NODATA_value -9999\n1 2\n3 4\n",
        encoding="utf-8",
    )
    layer = read_esri_ascii_grid(path, source_crs_epsg=32650)
    assert layer.source_crs_epsg == 32650


def test_seismic_multi_keeps_consistent_source_crs(tmp_path):
    from geomodel3d.dataio.seismic_io import read_seismic_multi

    first = tmp_path / "a.csv"
    second = tmp_path / "b.csv"
    first.write_text("lon,lat,depth\n116.3,39.9,1200\n", encoding="utf-8")
    second.write_text("lon,lat,depth\n116.5,39.7,1300\n", encoding="utf-8")
    layer = read_seismic_multi([first, second])
    assert layer.source_crs_epsg in (4490, 4326)


def test_seismic_multi_inconsistent_source_crs_warns(tmp_path, caplog):
    import logging

    from geomodel3d.dataio.seismic_io import read_seismic_multi

    first = tmp_path / "geo.csv"
    second = tmp_path / "proj.csv"
    first.write_text("lon,lat,depth\n116.3,39.9,1200\n", encoding="utf-8")
    second.write_text("x,y,z\n38500000,3000000,1200\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        layer = read_seismic_multi([first, second])
    assert layer.source_crs_epsg is None
