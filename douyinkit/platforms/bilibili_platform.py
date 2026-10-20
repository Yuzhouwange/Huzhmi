"""B 站适配器：把 `bilibili.py` 包成一个平台。"""

from __future__ import annotations

from .. import bilibili
from ..net import PlatformError
from .base import Platform


class BilibiliPlatform(Platform):
    name = "bilibili"
    label = "B站"
    page_size = 30
    supports_login = True
    url_pattern = r"bilibili\.com|b23\.tv|bili2233\.cn"
    profile_url_template = "https://space.bilibili.com/{id}"

    # ---------- 链接 ----------

    def _cookie(self, cfg: dict) -> str:
        # B 站设置里是单独的 bilibili_cookie；没填就退回通用 cookie
        return cfg.get("bilibili_cookie") or cfg.get("cookie") or ""

    def matches(self, url: str) -> bool:
        return bilibili.is_bilibili(url)

    def normalize(self, url: str) -> tuple[str, str]:
        return bilibili.normalize(url)

    def is_profile_url(self, url: str) -> bool:
        return bilibili.is_space_url(url)

    def resolve_short(self, url: str) -> str:
        return bilibili.resolve_short(url)

    # ---------- 内容 ----------

    def extract(self, url: str, cfg: dict) -> dict:
        return self._extract(url, cfg, allow_dash=True)

    def extract_fallback(self, url: str, cfg: dict) -> dict:
        # DASH 直链被 CDN 拒了：退回 fnval=1 的整段 MP4
        return self._extract(url, cfg, allow_dash=False)

    def _extract(self, url: str, cfg: dict, allow_dash: bool) -> dict:
        meta = bilibili.extract(
            url,
            cookie=self._cookie(cfg),
            proxy=cfg.get("proxy") or "",
            retries=int(cfg.get("retries") or 3),
            max_qn=int(cfg.get("bili_qn") or 0),
            allow_dash=allow_dash,
        )
        meta["platform"] = self.name
        return meta

    def episodes(self, url: str, cfg: dict) -> dict:
        """分 P / 合集选集。"""
        return bilibili.episodes(
            url,
            cookie=self._cookie(cfg),
            proxy=cfg.get("proxy") or "",
            retries=int(cfg.get("retries") or 3),
        )

    def profile(self, url: str, count: int, cfg: dict) -> dict:
        mid = bilibili.parse_mid(url)
        if not mid:
            raise PlatformError("没认出 UP 主：请粘贴 space.bilibili.com/<数字ID>")
        try:
            page = bilibili.user_videos(
                mid,
                page=1,
                cookie=self._cookie(cfg),
                proxy=cfg.get("proxy") or "",
                ps=int(count or self.page_size),
            )
        except bilibili.BiliError as exc:
            raise PlatformError(f"读取 UP 主失败：{exc}") from exc
        return {
            "profile": {
                "sec_uid": mid,
                "nickname": page["nickname"],
                "platform": self.name,
            },
            "items": page["items"],
            "has_more": page["has_more"],
            "max_cursor": page["page"] + 1,
            "total": page["total"],
        }

    def profile_more(self, token: str, cursor: int, count: int, cfg: dict) -> dict:
        try:
            page = bilibili.user_videos(
                token,
                page=max(1, cursor),
                cookie=self._cookie(cfg),
                proxy=cfg.get("proxy") or "",
                ps=int(count or self.page_size),
            )
        except bilibili.BiliError as exc:
            raise PlatformError(f"加载更多失败：{exc}") from exc
        return {
            "items": page["items"],
            "has_more": page["has_more"],
            "max_cursor": page["page"] + 1,
        }

    def item_source_url(self, video_id: str) -> str:
        return f"https://www.bilibili.com/video/{video_id}"

    # ---------- 下载 ----------

    def media_headers(self, cfg: dict) -> dict:
        headers = {
            "User-Agent": bilibili.UA,
            "Referer": "https://www.bilibili.com/",
            "Accept": "*/*",
        }
        cookie = self._cookie(cfg)
        if cookie:
            headers["Cookie"] = cookie
        return headers

    # ---------- 登录 ----------

    def login_state(self, cfg: dict) -> dict:
        state = bilibili.login_state()
        has_cookie = bool(cfg.get("bilibili_cookie"))
        if state.get("status") == "idle" and has_cookie:
            state = {
                "status": "saved",
                "message": "已保存 B 站登录 Cookie",
                "account": None,
            }
        return {**state, "has_cookie": has_cookie}

    def start_login(self) -> dict:
        return bilibili.start_login()

    def logout(self) -> dict:
        bilibili.logout()
        return bilibili.login_state()
