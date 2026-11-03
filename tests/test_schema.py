"""配置 schema 校验测试。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from geomodel3d.config.schema import (
    AppSettings,
    Bounds,
    CRSConfig,
    InterpConfig,
    ProjectConfig,
    ProjectMeta,
    ReserveConfig,
    StratigraphyEntry,
)


def test_bounds_valid():
    bounds = Bounds(min=[0, 0, -100], max=[100, 100, 0])
    assert bounds.size() == [100, 100, 100]


def test_bounds_invalid_order():
    with pytest.raises(ValidationError):
        Bounds(min=[0, 0, 0], max=[0, 100, 100])


def test_bounds_default_is_validated():
    # 仅传 min（max 用默认值）也应触发校验
    with pytest.raises(ValidationError):
        Bounds(min=[2000, 0, 0])


def test_reserve_metrics_distance_order():
    from geomodel3d.domain.reserve import ReserveMetrics

    with pytest.raises(ValidationError):
        ReserveMetrics(measured_distance=400)  # 默认 indicated=300 < 400


def test_app_settings_default_epsg_range():
    with pytest.raises(ValidationError):
        AppSettings(default_crs_epsg=10)


def test_project_config_duplicate_stratigraphy():
    meta = ProjectMeta(id="p", name="n")
    with pytest.raises(ValidationError):
        ProjectConfig(
            project=meta,
            stratigraphy=[
                StratigraphyEntry(layer_code="A", order=0),
                StratigraphyEntry(layer_code="A", order=1),
            ],
        )


def test_interp_method_validation():
    assert InterpConfig(method="idw").method == "idw"
    with pytest.raises(ValidationError):
        InterpConfig(method="unknown")


def test_interp_alias_roundtrip():
    config = InterpConfig(range_=500)
    dumped = config.model_dump(by_alias=True)
    assert dumped["range"] == 500
    assert InterpConfig.model_validate(dumped).range_ == 500


def test_crs_epsg_range():
    assert CRSConfig(epsg=32649).epsg == 32649
    with pytest.raises(ValidationError):
        CRSConfig(epsg=10)


def test_reserve_config_defaults():
    reserve = ReserveConfig()
    assert reserve.density == 2.6
    with pytest.raises(ValidationError):
        ReserveConfig(density=0)


def test_stratigraphy_order_required():
    with pytest.raises(ValidationError):
        StratigraphyEntry(layer_code="A")


def test_project_config_engine_policy():
    meta = ProjectMeta(id="p", name="n")
    assert ProjectConfig(project=meta).engine_policy == "auto"
    with pytest.raises(ValidationError):
        ProjectConfig(project=meta, engine_policy="bogus")


def test_app_settings_recent():
    settings = AppSettings()
    settings.push_recent("a")
    settings.push_recent("b")
    settings.push_recent("a")
    assert settings.recent_projects[:2] == ["a", "b"]


def test_app_settings_theme_and_level():
    with pytest.raises(ValidationError):
        AppSettings(theme="blue")
    with pytest.raises(ValidationError):
        AppSettings(log_level="TRACE")
