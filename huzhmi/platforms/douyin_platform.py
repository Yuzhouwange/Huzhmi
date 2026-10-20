"""抖音适配器：把 `douyin.py`（单条解析）和 `bridge.py`（主页列表）包成一个平台。

上层只看到 `Platform` 接口，不再关心「抖音要开浏览器、要预热、要复用 Cookie」。
"""

from __future__ import annotations

import urllib.parse

from .. import bridge as bridge_mod
from .. import douyin
from ..net import PlatformError, UA
from .base import Platform


class DouyinPlatform(Platform):
    name = "douyin"
    label = "抖音"
    # 未登录单页上限约 43 条（count=100 也只有 43）
    page_size = 50
    supports_login = True
    url_pattern = r"douyin\.com|iesdouyin\.com"
    profile_url_template = "https://www.douyin.com/user/{id}"

    # ---------- 链接 ----------

    def matches(self, url: str) -> bool:
        host = urllib.parse.urlsplit(url or "").netloc.lower()
        return "douyin.com" in host or "iesdouyin.com" in host

    def normalize(self, url: str) -> tuple[str, str]:
        return douyin.normalize(url)

    def is_profile_url(self, url: str) -> bool:
        return bridge_mod.is_profile_url(url)

    def resolve_short(self, url: str) -> str:
        return douyin.resolve_short(url)

    # ---------- 内容 ----------

    def extract(self, url: str, cfg: dict) -> dict:
        meta = douyin.extract(
            url,
            cookie=cfg.get("cookie") or "",
            proxy=cfg.get("proxy") or "",
            retries=int(cfg.get("retries") or 3),
            live_mode=str(cfg.get("live_photo") or "video"),
        )
        if meta.get("kind") == "images" and str(
                cfg.get("live_photo") or "video") != "image":
            # 分享页没有实况信息：借页面会话问一次详情，能拿到就换成视频
            meta = self._maybe_live(url, meta, str(cfg.get("live_photo") or "video"))
        meta["platform"] = self.name
        return meta

    def _maybe_live(self, url: str, meta: dict, live_mode: str) -> dict:
        """图集作品再查一次详情：是实况就按 live_mode 换成 mp4（或图+视频）。"""
        aweme_id = str(meta.get("aweme_id") or "")
        if not aweme_id:
            return meta
        try:
            detail = bridge_mod.Bridge.get().aweme_detail(aweme_id)
        except Exception:  # noqa: BLE001 - 桥接不可用就当普通图集
            return meta
        images = (detail or {}).get("images") or []
        if not any(isinstance(im, dict) and im.get("video") for im in images):
            return meta
        try:
            rebuilt = douyin._build_meta(
                detail, meta.get("source_url") or url, live_mode
            )
        except Exception:  # noqa: BLE001
            return meta
        rebuilt["platform"] = meta.get("platform")
        if meta.get("strategy"):
            rebuilt["strategy"] = meta["strategy"]
        return rebuilt

    def profile(self, url: str, count: int, cfg: dict) -> dict:
        bridge = bridge_mod.Bridge.get()
        try:
            info = bridge.resolve_profile(url)
            page = bridge.user_posts(info["sec_uid"], 0, count or self.page_size)
        except Exception as exc:  # noqa: BLE001
            raise PlatformError(f"读取主页失败：{exc}") from exc
        if not info.get("nickname") and page.get("items"):
            info["nickname"] = (page["items"][0].get("author") or "").strip()
        has_cookie = bool(cfg.get("cookie"))
        needs_login = bool(info.get("needs_login") or page.get("needs_login"))
        return {
            "profile": {
                **info,
                "platform": self.name,
                "needs_login": needs_login,
                "has_cookie": has_cookie,
            },
            "items": page["items"],
            "has_more": page["has_more"],
            "max_cursor": page["max_cursor"],
            "total": info.get("total_works") or 0,
            "needs_login": needs_login,
        }

    def profile_more(self, token: str, cursor: int, count: int, cfg: dict) -> dict:
        try:
            return bridge_mod.Bridge.get().user_posts(
                token, cursor, count or self.page_size
            )
        except Exception as exc:  # noqa: BLE001
            raise PlatformError(f"加载更多失败：{exc}") from exc

    def item_source_url(self, video_id: str) -> str:
        return f"https://www.douyin.com/video/{video_id}"

    # ---------- 下载 ----------

    def media_headers(self, cfg: dict) -> dict:
        headers = {
            "User-Agent": UA,
            "Referer": "https://www.douyin.com/",
            "Accept": "*/*",
        }
        if cfg.get("cookie"):
            headers["Cookie"] = cfg["cookie"]
        return headers

    # ---------- 登录 ----------

    def login_state(self, cfg: dict) -> dict:
        state = douyin.login_state()
        has_cookie = bool(cfg.get("cookie"))
        if state.get("status") == "idle" and has_cookie:
            state = {
                "status": "saved",
                "message": "已保存抖音登录 Cookie",
                "account": None,
            }
        return {**state, "has_cookie": has_cookie}

    def start_login(self) -> dict:
        return douyin.start_login()

    def logout(self) -> dict:
        douyin.logout()
        return douyin.login_state()
