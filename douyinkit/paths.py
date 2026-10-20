"""开发态 / 打包态（PyInstaller）的路径判断。

打包成 exe 之后有两条完全不同的路径：

  - **只读资源**：`web/` 这些随包文件被解到 `sys._MEIPASS`（onefile 是个临时目录）
  - **可写数据**：配置和 SQLite 不能写进临时目录，否则每次启动都丢

所以资源和数据分开取；开发态两者都在仓库根目录下，行为和以前一致。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

APP_NAME = "DouyinKit"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """只读资源根目录（里面有 web/）。"""
    if is_frozen():
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            return Path(bundle)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def data_dir() -> Path:
    """可写数据目录（config.json / douyinkit.db）。

    优先级：
      1. `DOUZY_DATA_DIR` 环境变量（测试和自定义部署用）
      2. 打包态：exe 同级的 `data/`（便携，拷走整个文件夹就能带走数据）
      3. 打包态：`%LOCALAPPDATA%\\DouyinKit\\data`（exe 放在 Program Files 这种只读位置时）
      4. 开发态：仓库下的 `data/`
    """
    override = os.environ.get("DOUZY_DATA_DIR")
    if override:
        path = Path(override).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    if is_frozen():
        portable = Path(sys.executable).resolve().parent / "data"
        if _writable(portable):
            return portable
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        fallback = Path(base) / APP_NAME / "data"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

    return Path(__file__).resolve().parent.parent / "data"


def temp_dir(prefix: str = "douyinkit-") -> Path:
    """临时目录（打包态不要用仓库路径）。"""
    return Path(tempfile.mkdtemp(prefix=prefix))
