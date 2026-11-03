"""崩溃转储（NFR 稳定性）：faulthandler 致命栈 + Windows minidump。"""
from __future__ import annotations

import faulthandler
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, TextIO

logger = logging.getLogger(__name__)

_FAULTHANDLER_FILE: Optional[TextIO] = None


def enable_faulthandler(log_dir: Path) -> Optional[Path]:
    """把致命信号/访问冲突的栈写入 ``faulthandler.log``。

    返回日志路径；重复调用会先关闭旧文件，避免测试或多上下文场景泄漏句柄。
    """
    global _FAULTHANDLER_FILE
    path = Path(log_dir) / "faulthandler.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        disable_faulthandler()
        handle = open(path, "a", encoding="utf-8")
        faulthandler.enable(file=handle, all_threads=True)
        _FAULTHANDLER_FILE = handle
        return path
    except Exception:  # noqa: BLE001 - 崩溃转储不可用不应阻断应用
        logger.exception("启用 faulthandler 失败: %s", path)
        return None


def disable_faulthandler() -> None:
    global _FAULTHANDLER_FILE
    try:
        faulthandler.disable()
    except Exception:  # noqa: BLE001
        pass
    if _FAULTHANDLER_FILE is not None:
        try:
            _FAULTHANDLER_FILE.close()
        except Exception:  # noqa: BLE001
            pass
    _FAULTHANDLER_FILE = None


def write_minidump(directory: Path, *, prefix: str = "crash") -> Optional[Path]:
    """在当前进程内写一个 Windows minidump；非 Windows 或失败时返回 ``None``。"""
    if sys.platform != "win32":
        return None

    directory = Path(directory)
    path = directory / (
        f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.dmp"
    )
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        directory.mkdir(parents=True, exist_ok=True)
        dbghelp = ctypes.WinDLL("dbghelp.dll", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetCurrentProcessId.restype = wintypes.DWORD

        dump = dbghelp.MiniDumpWriteDump
        dump.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.HANDLE,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        dump.restype = wintypes.BOOL

        with open(path, "wb") as handle:
            os_handle = msvcrt.get_osfhandle(handle.fileno())
            ok = dump(
                kernel32.GetCurrentProcess(),
                kernel32.GetCurrentProcessId(),
                wintypes.HANDLE(os_handle),
                0,  # MiniDumpNormal：体积较小，保留线程/模块/栈信息
                None,
                None,
                None,
            )
            if not ok:
                raise OSError(ctypes.get_last_error(), "MiniDumpWriteDump 失败")
        if path.stat().st_size == 0:
            raise OSError("minidump 文件为空")
        logger.info("已写入崩溃转储: %s", path)
        return path
    except Exception:  # noqa: BLE001 - 转储失败不能影响错误上报
        logger.exception("写入崩溃转储失败: %s", path)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None


__all__ = ["enable_faulthandler", "disable_faulthandler", "write_minidump"]
