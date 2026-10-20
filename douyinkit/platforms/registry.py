"""平台注册表：按链接/名字找到对应适配器。

新增平台只改这里一处；engine、server、前端都从这里取，不再出现
`if platform == "bilibili"` 这样的分叉。
"""

from __future__ import annotations

from .base import Platform
from .bilibili_platform import BilibiliPlatform
from .douyin_platform import DouyinPlatform
from .tiktok_platform import TiktokPlatform

# 顺序有意义：先匹配到的赢。TikTok 必须在抖音前面，避免被兜底吃掉。
PLATFORMS: tuple[Platform, ...] = (
    BilibiliPlatform(), TiktokPlatform(), DouyinPlatform()
)

BY_NAME: dict[str, Platform] = {platform.name: platform for platform in PLATFORMS}

# 认不出平台时按这个处理，保持和重构前一致
FALLBACK = "douyin"


def all_platforms() -> tuple[Platform, ...]:
    return PLATFORMS


def by_name(name: str) -> Platform:
    """按平台名取适配器；未知名字抛 KeyError。"""
    platform = BY_NAME.get((name or "").strip())
    if platform is None:
        raise KeyError(f"未知平台：{name}")
    return platform


def find(name: str | None) -> Platform | None:
    return BY_NAME.get((name or "").strip())


def resolve(url: str) -> Platform:
    """按链接识别平台，认不出来按抖音处理（与重构前一致）。"""
    for platform in PLATFORMS:
        try:
            if platform.matches(url):
                return platform
        except Exception:  # noqa: BLE001 - 识别失败不该炸整个流程
            continue
    return BY_NAME[FALLBACK]


def describe() -> list[dict]:
    """给前端：平台名、显示名、单页条数、识别用正则、是否支持登录。"""
    return [platform.describe() for platform in PLATFORMS]
