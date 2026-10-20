#!/usr/bin/env python3
"""用 Inno Setup 打 Windows 安装包（内置 ffmpeg）。

    python build_installer.py

需要先 `python build_exe.py` 生成 dist/Huzhmi.exe。
Inno Setup 6 可以用 winget 安装：winget install JRSoftware.InnoSetup
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ISS = ROOT / "installer" / "Huzhmi.iss"
EXE = ROOT / "dist" / "Huzhmi.exe"
FFMPEG = ROOT / "data" / "tools" / "ffmpeg.exe"


def find_iscc() -> Path | None:
    found = shutil.which("iscc")
    if found:
        return Path(found)
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]
    return next((path for path in candidates if path.is_file()), None)


def main() -> None:
    if not EXE.is_file():
        raise SystemExit("[installer] 先运行 build_exe.py 生成 dist/Huzhmi.exe")
    if not FFMPEG.is_file():
        raise SystemExit("[installer] 缺少 data/tools/ffmpeg.exe")
    iscc = find_iscc()
    if iscc is None:
        raise SystemExit(
            "[installer] 没找到 ISCC.exe；先执行：winget install JRSoftware.InnoSetup")
    print(f"[installer] {iscc} {ISS}")
    subprocess.run([str(iscc), str(ISS)], check=True)
    target = ROOT / "dist" / "Huzhmi-Setup-1.0.exe"
    if not target.is_file():
        raise SystemExit("[installer] 编译结束但没找到安装包")
    size = target.stat().st_size / (1024 * 1024)
    print(f"\n[installer] 完成：{target}  ({size:.1f} MB)")


if __name__ == "__main__":
    main()
