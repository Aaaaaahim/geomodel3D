"""崩溃转储测试。"""
from __future__ import annotations

import sys

import pytest

from geomodel3d.app.crash_dump import (
    disable_faulthandler,
    enable_faulthandler,
    write_minidump,
)


def test_faulthandler_log_created(tmp_path):
    path = enable_faulthandler(tmp_path)
    try:
        assert path is not None
        assert path.exists()
    finally:
        disable_faulthandler()


@pytest.mark.skipif(sys.platform != "win32", reason="minidump 仅在 Windows 可用")
def test_write_minidump(tmp_path):
    path = write_minidump(tmp_path)
    assert path is not None
    assert path.exists()
    assert path.stat().st_size > 0
