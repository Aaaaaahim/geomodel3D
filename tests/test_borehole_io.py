"""钻孔解析测试（M1-F1 / 8.2）。"""
from __future__ import annotations

import pandas as pd
import pytest

from geomodel3d.config.errors import GeoModelError
from geomodel3d.dataio.borehole_io import detect_mapping, read_boreholes

CSV = (
    "孔号,东坐标,北坐标,孔口高程,层顶深,层底深,地层代码,品位\n"
    "H1,0,0,100,0,20,A,1.2\n"
    "H1,0,0,100,20,50,B,0.4\n"
    "H2,200,200,120,0,25,A,0.9\n"
    "H2,200,200,120,25,60,B,0.3\n"
)


def test_detect_mapping_chinese():
    mapping = detect_mapping(["孔号", "东坐标", "北坐标", "孔口高程", "层顶深", "层底深", "地层代码"])
    assert mapping["hole_id"] == "孔号"
    assert mapping["x"] == "东坐标"
    assert mapping["layer_code"] == "地层代码"


def test_read_boreholes_groups(tmp_path):
    path = tmp_path / "bh.csv"
    path.write_text(CSV, encoding="utf-8-sig")
    holes = read_boreholes(path)
    assert len(holes) == 2
    h1 = next(h for h in holes if h.hole_id == "H1")
    assert len(h1.intervals) == 2
    assert h1.total_depth == 50
    assert h1.intervals[0].thickness == 20


def test_missing_required_column(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("孔号,东坐标,北坐标\nH1,0,0\n", encoding="utf-8")
    with pytest.raises(GeoModelError):
        read_boreholes(path)


def test_excel_roundtrip(tmp_path):
    pytest.importorskip("openpyxl")
    frame = pd.DataFrame(
        {
            "hole_id": ["H1", "H1"],
            "x": [0, 0],
            "y": [0, 0],
            "collar_elev": [100, 100],
            "layer_top": [0, 20],
            "layer_base": [20, 40],
            "layer_code": ["A", "B"],
        }
    )
    path = tmp_path / "bh.xlsx"
    frame.to_excel(path, index=False)
    holes = read_boreholes(path)
    assert len(holes) == 1
    assert len(holes[0].intervals) == 2


ELEV_CSV = (
    "X,Y,Z_surface,hole_id,layer_no,formation_name,top_elev,bottom_elev,thickness,grade\n"
    "0,0,331.29,H1,1,Overburden,331.29,318.06,13.23,0.02\n"
    "0,0,331.29,H1,2,Oxidized,331.29,308.61,22.68,0.70\n"
    "0,0,331.29,H1,3,Mineralized,331.29,294.91,36.38,5.63\n"
)


def test_detect_mapping_english_elevation():
    mapping = detect_mapping(
        ["X", "Y", "Z_surface", "hole_id", "layer_no", "formation_name",
         "top_elev", "bottom_elev", "thickness", "grade"]
    )
    assert mapping["x"] == "X"
    assert mapping["collar_elev"] == "Z_surface"
    assert mapping["layer_code"] == "formation_name"
    assert mapping["layer_top_elev"] == "top_elev"
    assert mapping["layer_base_elev"] == "bottom_elev"
    # 标高列不应被误判为深度列
    assert mapping["layer_top"] is None
    assert mapping["layer_base"] is None


def test_read_boreholes_elevation_cumulative(tmp_path):
    """顶标高恒等于孔口、厚度为累计厚度的数据应按层底深重建连续层序。"""
    path = tmp_path / "ref.csv"
    path.write_text(ELEV_CSV, encoding="utf-8")
    holes = read_boreholes(path)
    assert len(holes) == 1
    intervals = holes[0].intervals
    assert [iv.layer_code for iv in intervals] == ["Overburden", "Oxidized", "Mineralized"]
    assert intervals[0].depth_top == pytest.approx(0.0)
    assert intervals[0].depth_bottom == pytest.approx(13.23)
    assert intervals[1].depth_top == pytest.approx(13.23)
    assert intervals[1].depth_bottom == pytest.approx(22.68)
    assert intervals[2].depth_top == pytest.approx(22.68)
    assert intervals[2].depth_bottom == pytest.approx(36.38)
    assert holes[0].total_depth == pytest.approx(36.38)
