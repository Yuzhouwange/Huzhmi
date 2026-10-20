"""用 Edge / Chrome 的 `--app` 模式开一个独立窗口。

和「开浏览器」的区别：没有地址栏、没有标签页、没有书签栏，任务栏里是一个
独立窗口，看着就是本地软件。代价是仍然复用系统里的 Chromium 内核——好处是
不用额外装 WebView2 运行时，打包体积也不变。

窗口关掉（`proc.wait()` 返回）就当作退出信号，主程序跟着收尾。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .cdp import find_browser


def available() -> str | None:
    """有能开 app 窗口的 Chromium 就返回它的路径。"""
    return find_browser()


def _args(browser: str, url: str, profile_dir: Path) -> list[str]:
    return [
        browser,
        f"--app={url}",
        "--window-size=1280,900",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=TranslateUI",
        "--disable-session-crashed-bubble",
        "--hide-crash-restore-bubble",
        # 独立 profile 必须带上，否则会去接管用户已经开着的浏览器进程
        "--new-window",
    ]


def open_window(url: str, profile_dir: str | Path) -> subprocess.Popen | None:
    """打开独立窗口；没有可用浏览器或启动失败返回 None（调用方退回浏览器）。"""
    browser = available()
    if not browser:
        return None
    root = Path(profile_dir)
    # 每次启动用独立 profile：同一个 profile 被旧 Edge 占用时，新进程会把
    # 窗口交给旧进程后立刻退出，主程序误以为“窗口关了”跟着退出，留下一个
    # 打不开接口的僵尸窗口。独立 profile 保证 proc.wait() 跟窗口同生命周期。
    profile = root / f"run-{os.getpid()}"
    profile.mkdir(parents=True, exist_ok=True)
    for old in root.glob("run-*"):
        if old != profile:
            shutil.rmtree(old, ignore_errors=True)

    creationflags = 0
    if sys.platform == "win32":
        # 别跟着控制台一起弹黑框（窗口版 exe 里本来也没有控制台）
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        return subprocess.Popen(
            _args(browser, url, profile),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except OSError:
        return None
