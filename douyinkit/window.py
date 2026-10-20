"""用 Edge / Chrome 的 `--app` 模式开一个独立窗口。

和「开浏览器」的区别：没有地址栏、没有标签页、没有书签栏，任务栏里是一个
独立窗口，看着就是本地软件。代价是仍然复用系统里的 Chromium 内核——好处是
不用额外装 WebView2 运行时，打包体积也不变。

窗口关掉（`proc.wait()` 返回）就当作退出信号，主程序跟着收尾。
"""

from __future__ import annotations

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
        # 独立 profile 必须带上，否则会去接管用户已经开着的浏览器进程
        "--new-window",
    ]


def open_window(url: str, profile_dir: str | Path) -> subprocess.Popen | None:
    """打开独立窗口；没有可用浏览器或启动失败返回 None（调用方退回浏览器）。"""
    browser = available()
    if not browser:
        return None
    profile = Path(profile_dir)
    profile.mkdir(parents=True, exist_ok=True)

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
