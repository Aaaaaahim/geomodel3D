"""坐标系自动识别测试（M1-F5）。"""
from __future__ import annotations

import pytest

from geomodel3d.dataio.crs_detect import best_epsg, detect_crs


def test_detect_geographic_china():
    suggestion = detect_crs([[116.3, 39.9], [116.4, 39.8]])[0]
    assert suggestion.epsg == 4490
    assert suggestion.confidence == "high"


def test_detect_geographic_global():
    suggestion = detect_crs([[-70.0, 40.0], [-71.0, 41.0]])[0]
    assert suggestion.epsg == 4326
    assert suggestion.confidence == "high"


def test_detect_zone_prefixed_3degree():
    # 3°带 第38带（中央经线 114°E）含带号前缀 38,500,000
    suggestion = detect_crs([[38_500_000.0, 3_460_000.0], [38_520_000.0, 3_470_000.0]])[0]
    assert suggestion.epsg == 4526  # CGCS2000 / 3-degree GK zone 38
    assert suggestion.confidence == "medium"


def test_detect_zone_prefixed_6degree():
    # 6°带 第17带（中央经线 99°E）
    suggestion = detect_crs([[17_500_000.0, 3_460_000.0], [17_600_000.0, 3_480_000.0]])[0]
    assert suggestion.epsg == 4495  # CGCS2000 / GK zone 17
    assert suggestion.confidence == "medium"


def test_detect_projected_without_zone_is_ambiguous():
    # UTM 无带号前缀（东坐标 6 位），无法判定带号
    suggestion = detect_crs([[499_200.0, 3_460_200.0], [500_100.0, 3_470_000.0]])[0]
    assert suggestion.epsg is None
    assert suggestion.confidence == "low"
    assert best_epsg([[499_200.0, 3_460_200.0]]) is None


def test_detect_empty():
    assert detect_crs([])[0].epsg is None
