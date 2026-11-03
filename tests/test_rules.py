"""业务规则与数据质量校验测试（BR-02）。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from geomodel3d.domain.entities import Drillhole, DrillholeInterval
from geomodel3d.domain.rules import Severity, validate_boreholes


def _hole(hole_id="H1", top=0.0, bottom=20.0, layer="A", grade=None):
    return Drillhole(
        hole_id=hole_id,
        x=0.0,
        y=0.0,
        collar_elev=100.0,
        total_depth=bottom,
        intervals=[
            DrillholeInterval(layer_code=layer, depth_top=top, depth_bottom=bottom, grade=grade)
        ],
    )


def test_valid_holes_pass():
    report = validate_boreholes([_hole(), _hole("H2")], valid_layers=["A"])
    assert report.passed
    assert report.summary()["error"] == 0


def test_interval_order_error():
    hole = Drillhole(
        hole_id="H1",
        x=0,
        y=0,
        collar_elev=100,
        intervals=[
            DrillholeInterval(layer_code="A", depth_top=20, depth_bottom=40),
            DrillholeInterval(layer_code="B", depth_top=30, depth_bottom=50),
        ],
    )
    report = validate_boreholes([hole])
    assert not report.passed
    assert any(i.code == "DAT-VAL-ORD" for i in report.errors)


def test_duplicate_hole_error():
    report = validate_boreholes([_hole(), _hole()])
    assert any(i.code == "DAT-VAL-DUP" for i in report.errors)


def test_unknown_layer_error():
    report = validate_boreholes([_hole(layer="Z")], valid_layers=["A"])
    assert any(i.code == "DAT-VAL-LYR" for i in report.errors)


def test_grade_out_of_range_warns():
    report = validate_boreholes([_hole(grade=999)], grade_range=(0, 100))
    assert report.passed
    assert any(i.severity == Severity.WARNING and i.code == "DAT-VAL-GRD" for i in report.warnings)


def test_bounds_warning():
    report = validate_boreholes([_hole()], bounds=[[100, 100, 0], [200, 200, 200]])
    assert report.passed
    assert any(i.code == "DAT-VAL-BND" for i in report.warnings)


def test_derive_stratigraphy():
    from geomodel3d.domain.rules import derive_stratigraphy

    hole = Drillhole(
        hole_id="H1", x=0, y=0, collar_elev=100,
        intervals=[
            DrillholeInterval(layer_code="A", depth_top=0, depth_bottom=20),
            DrillholeInterval(layer_code="B", depth_top=20, depth_bottom=50),
        ],
    )
    entries = derive_stratigraphy([hole])
    assert [entry.layer_code for entry in entries] == ["A", "B"]
    assert [entry.order for entry in entries] == [0, 1]


def test_interval_negative_depth_rejected():
    with pytest.raises(ValidationError):
        DrillholeInterval(layer_code="A", depth_top=-1.0, depth_bottom=10.0)


def test_interval_thickness_mismatch_rejected():
    with pytest.raises(ValidationError):
        DrillholeInterval(layer_code="A", depth_top=0.0, depth_bottom=10.0, thickness=20.0)
