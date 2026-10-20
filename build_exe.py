#!/usr/bin/env python3
"""把 DouyinKit 打包成单文件 exe（PyInstaller）。

    python build_exe.py              # 打包到 dist/DouyinKit.exe（无控制台窗口）
    python build_exe.py --console    # 保留控制台，方便看报错
    python build_exe.py --no-venv    # 直接用当前 Python 环境里的 PyInstaller
    python build_exe.py --onedir     # 目录版（启动更快，便于排错）

为什么默认建独立 venv：PyInstaller 只用来打包，不该污染日常使用的 Python
环境；`.build-venv/` 删掉就干净了。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".build-venv"
DIST = ROOT / "dist"
BUILD = ROOT / "build"


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def ensure_venv(with_venv: bool) -> Path:
    if not with_venv:
        return Path(sys.executable)
    python = venv_python()
    if not python.is_file():
        print(f"[build] 创建打包用虚拟环境：{VENV}")
        venv.EnvBuilder(with_pip=True, clear=False).create(VENV)
    return python


def ensure_pyinstaller(python: Path) -> None:
    check = subprocess.run(
        [str(python), "-c", "import PyInstaller; print(PyInstaller.__version__)"],
        capture_output=True, text=True,
    )
    if check.returncode == 0:
        print(f"[build] PyInstaller {check.stdout.strip()} 已就绪")
        return
    print("[build] 安装 PyInstaller…")
    subprocess.run(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check",
         "--quiet", "pyinstaller"],
        check=True,
    )


def build(onedir: bool, with_venv: bool, clean: bool,
          console: bool = False) -> Path:
    python = ensure_venv(with_venv)
    ensure_pyinstaller(python)

    icon = ROOT / "logo.ico"
    mode = "--onedir" if onedir else "--onefile"
    args = [
        str(python), "-m", "PyInstaller",
        str(ROOT / "run.py"),
        "--name", "DouyinKit",
        mode,
        # 默认不带控制台：双击就是一个干净的独立窗口（日志写 data/log.txt）
        "--console" if console else "--windowed",
        "--noconfirm",
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--specpath", str(BUILD),
        # 前端页面必须随包走；运行时从 sys._MEIPASS/web 读
        "--add-data", f"{ROOT / 'web'}{';' if sys.platform == 'win32' else ':'}web",
        # 平台适配器是运行时按名字取的，显式收集一遍更保险
        "--collect-submodules", "douyinkit",
    ]
    if icon.is_file():
        args += ["--icon", str(icon)]
    if clean:
        args.append("--clean")

    print("[build] " + " ".join(args[1:]))
    subprocess.run(args, check=True)

    target = DIST / ("DouyinKit/DouyinKit.exe" if onedir else "DouyinKit.exe")
    if not target.is_file():  # 非 Windows 的可执行文件名不带后缀
        target = DIST / ("DouyinKit/DouyinKit" if onedir else "DouyinKit")
    if not target.is_file():
        raise SystemExit("[build] 没找到产物，检查上面的 PyInstaller 输出")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(prog="build_exe")
    parser.add_argument("--no-venv", action="store_true", help="不建独立 venv")
    parser.add_argument("--onedir", action="store_true", help="打成目录版而非单文件")
    parser.add_argument("--no-clean", action="store_true", help="不复用缓存，跳过 --clean")
    parser.add_argument("--console", action="store_true",
                        help="保留控制台窗口（排错用；默认无控制台）")
    args = parser.parse_args()

    target = build(onedir=args.onedir, with_venv=not args.no_venv,
                   clean=not args.no_clean, console=args.console)
    size = target.stat().st_size / (1024 * 1024)
    print(f"\n[build] 完成：{target}  ({size:.1f} MB)")
    print("[build] 双击运行（独立窗口），或：\n"
          f'        "{target}" --no-browser --port 8765   # 只起服务')
    print("[build] 数据目录：exe 同级 data/（不可写时退回 "
          "%LOCALAPPDATA%\\DouyinKit\\data）")


if __name__ == "__main__":
    main()
