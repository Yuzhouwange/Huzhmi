"""单实例互斥：同一个用户只允许跑一个 DouyinKit。

Windows 用命名 Mutex（跨进程、随进程退出自动释放）；其他平台退回文件锁。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_NAME = "Local\\DouyinKit.SingleInstance"
_handle = None
_lock_file = None


def acquire() -> bool:
    global _handle, _lock_file
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                                          wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, _NAME)
        if not handle:
            return False
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return False
        _handle = handle
        return True
    try:
        import fcntl

        path = Path(tempfile.gettempdir()) / "douyinkit.lock"
        _lock_file = path.open("w")
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_file.write(str(os.getpid()))
        _lock_file.flush()
        return True
    except Exception:  # noqa: BLE001
        return False


def release() -> None:
    global _handle, _lock_file
    if sys.platform == "win32" and _handle:
        try:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(_handle)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        _handle = None
    if _lock_file is not None:
        try:
            _lock_file.close()
        except OSError:
            pass
        _lock_file = None
