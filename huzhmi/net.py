"""两个平台共用的东西：UA、文本解码、扩展名推断、异常基类。

以前 `bilibili.py` 里写着 `from .douyin import decode_text`——B 站解析为了一个
文本解码函数去依赖抖音模块，两个平台就此绑死。现在这类公共零件集中在这里，
平台之间不再互相 import。
"""

from __future__ import annotations

import urllib.parse

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)

IMAGE_EXT = (".jpeg", ".jpg", ".png", ".webp", ".heic")
VIDEO_EXT = (".mp4", ".mov", ".m4v")


class PlatformError(Exception):
    """解析/抓取失败。消息直接展示给用户。

    抖音的 `douyin.ExtractError`、B 站的 `bilibili.BiliError` 都是它的子类，
    上层只认这一个类型，不用再按平台分叉 except。
    """


def decode_text(raw: bytes) -> str:
    """页面/接口文本解码：优先 UTF-8，失败退 GB18030，最后才丢字节。

    直接用 `errors="ignore"` 会在编码不对时把中文拆成残缺字节，落盘就是乱码。
    """
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def guess_ext(url: str, kind: str) -> str:
    """按 URL 后缀猜扩展名；猜不到就用该类型的默认值。"""
    path = urllib.parse.urlsplit(url).path.lower()
    for ext in VIDEO_EXT if kind == "video" else IMAGE_EXT:
        if path.endswith(ext):
            return ext
    return ".mp4" if kind == "video" else ".jpg"
