"""媒体处理层：把「转换 / 提取」做成一堆可插拔的 `Transform`。

分工很明确：

* **不依赖外部工具**的活（提取音轨、重封装、裁切）走纯 Python，永远可用；
* **需要编码器**的活（mp3/flac/wav、视频转码）调 ffmpeg 子进程，找不到工具
  就明确报 `ToolMissing`，由界面提示去配置——不会静默失败。
"""

from . import library  # noqa: F401
from . import tags  # noqa: F401
from . import verify  # noqa: F401
from .transforms import (  # noqa: F401
    ToolMissing,
    Transform,
    TransformError,
    describe,
    get,
    names,
)

__all__ = [
    "library",
    "tags",
    "verify",
    "ToolMissing",
    "Transform",
    "TransformError",
    "describe",
    "get",
    "names",
]
