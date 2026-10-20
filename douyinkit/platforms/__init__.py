"""平台适配器：抖音、B 站各自一个模块，公共约定在 `base.Platform`。"""

from __future__ import annotations

from .base import Platform
from .registry import all_platforms, by_name, describe, find, resolve

__all__ = ["Platform", "all_platforms", "by_name", "describe", "find", "resolve"]
