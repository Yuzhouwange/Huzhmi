#!/usr/bin/env python3
"""Huzhmi 启动入口。

    python run.py                # 独立窗口（没有地址栏，像本地软件）
    python run.py --no-window    # 退回默认浏览器
    python run.py --no-browser   # 只起服务，不开任何界面
    python run.py --port 9000
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path


def _log_file() -> Path | None:
    """窗口版 exe 没有控制台，把日志落到 data/log.txt。"""
    try:
        from huzhmi.paths import data_dir

        target = Path(data_dir()) / "log.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        return target
    except Exception:  # noqa: BLE001 - 日志落不了盘也不能挡住启动
        return None


def _redirect_output() -> None:
    """无控制台运行时，把 stdout/stderr 接到日志文件，否则 print 会崩。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    path = _log_file()
    if path is None:
        return
    handle = open(path, "a", encoding="utf-8", buffering=1)
    sys.stdout = handle
    sys.stderr = handle


def _fatal(message: str) -> None:
    """启动失败时给个 Windows 弹窗，别让人对着黑屏发呆。"""
    _redirect_output()
    print(message, flush=True)
    try:
        import ctypes

        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
                None, message[:900], "Huzhmi 启动失败", 0x10
            )
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    _redirect_output()
    parser = argparse.ArgumentParser(prog="huzhmi")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="端口，默认 8765")
    parser.add_argument("--no-window", action="store_true",
                        help="不开独立窗口，改用默认浏览器")
    parser.add_argument("--no-browser", action="store_true",
                        help="只起服务，不开任何界面（调试用）")
    args = parser.parse_args()

    from huzhmi import single_instance
    from huzhmi.server import serve

    if not single_instance.acquire():
        _redirect_output()
        message = "Huzhmi 已经在运行。\n\n请使用已经打开的窗口；如果找不到，请在任务管理器结束 Huzhmi 后重试。"
        print(message, flush=True)
        try:
            import ctypes

            if sys.platform == "win32" and (getattr(sys, "frozen", False)
                                            or sys.stdout is None):
                ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
                    None, message, "Huzhmi", 0x40)
        except Exception:  # noqa: BLE001
            pass
        return

    restart = False
    try:
        restart = serve(
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser and not args.no_window,
            window=not args.no_browser and not args.no_window,
        )
    finally:
        single_instance.release()

    if restart:
        # 先让旧进程把端口/数据库/浏览器放掉，再拉新进程
        time.sleep(1.0)
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command.append(str(Path(__file__).resolve()))
        command += sys.argv[1:]
        try:
            base = (Path(sys.executable).resolve().parent
                    if getattr(sys, "frozen", False)
                    else Path(__file__).resolve().parent)
            subprocess.Popen(command, cwd=str(base))
        except OSError as exc:
            _fatal(f"重启失败：{exc}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        text = traceback.format_exc()
        _fatal(f"程序异常退出：\n\n{text}")
        os._exit(1)
