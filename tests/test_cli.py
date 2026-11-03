"""headless 批处理运行器测试（M10-F4）。"""
from __future__ import annotations

from pathlib import Path

from geomodel3d.jobs.runner import _repair_options, _safe_name, run_job

JOB = Path(__file__).resolve().parents[1] / "examples" / "demo" / "job.json"


def test_safe_name_blocks_path_traversal():
    assert _safe_name("../../evil") == "evil"
    assert _safe_name("a b/c") == "abc"
    assert _safe_name("") == "untitled"


def test_repair_options_from_job():
    assert _repair_options(True) is None
    options = _repair_options(
        {
            "fill_holes": False,
            "fix_layer_order": True,
            "simplify_tolerance": 0.5,
        }
    )
    assert options.fill_holes is False
    assert options.fix_layer_order is True
    assert options.simplify_tolerance == 0.5


def test_run_job_end_to_end(tmp_path):
    assert JOB.exists(), f"示例 Job 缺失: {JOB}"
    result = run_job(JOB, tmp_path)
    assert result["status"] == "finished"
    assert result["cells"] > 0
    assert result["volume"] > 0
    assert Path(result["project"]).exists()
    assert (tmp_path / "result.json").exists()
    assert any(export.endswith(".vtu") for export in result["exports"])
    assert result["reserve"]["tonnage"] > 0
    assert any(export.endswith(".geojson") for export in result["exports"])
    assert result["repair"]
    assert result["iterations"]
    assert (tmp_path / "iterate_summary.json").exists()
