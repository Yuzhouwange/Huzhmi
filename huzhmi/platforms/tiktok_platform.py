"""TikTok 单条公开视频 / 图集。

只做公开页面能拿到的内容：单条视频、图集。主页批量需要签名接口，暂不硬凑。
页面数据在 `__UNIVERSAL_DATA_FOR_REHYDRATION__` 里，和抖音分享页思路一致。
"""

from __future__ import annotations

import html as html_mod
import http.cookiejar
import json
import re
import urllib.parse
import urllib.request

from ..net import PlatformError, UA
from .base import Platform

_VIDEO_RE = re.compile(r"/video/(\d{8,25})")
_SHORT_HOSTS = ("vm.tiktok.com", "vt.tiktok.com", "vt.tiktok.com")
_DATA_RE = re.compile(
    r'id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', re.S)
# TikTok 的 WAF 对带 "Gecko" 的完整 UA 会直接回挑战页；这个精简 UA 实测能拿数据
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "Chrome/139.0.0.0 Safari/537.36")


def _fetch(url: str) -> tuple[str, str]:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", _UA),
                         ("Accept", "text/html,application/xhtml+xml")]
    with opener.open(url, timeout=30) as response:
        page = response.read().decode("utf-8", "replace")
    cookies = "; ".join(f"{item.name}={item.value}" for item in jar)
    return page, cookies


def _scope(url: str) -> tuple[dict, str]:
    page, cookies = _fetch(url)
    match = _DATA_RE.search(page)
    if not match:
        raise PlatformError("TikTok 页面没有数据，可能需要验证或作品不可见")
    try:
        data = json.loads(html_mod.unescape(match.group(1)))
    except ValueError as exc:
        raise PlatformError(f"TikTok 页面数据解析失败：{exc}") from exc
    return (data.get("__DEFAULT_SCOPE__") or {}), cookies


def _video_id(url: str) -> str:
    match = _VIDEO_RE.search(url or "")
    return match.group(1) if match else ""


class TiktokPlatform(Platform):
    name = "tiktok"
    label = "TikTok"
    page_size = 30
    supports_login = False
    url_pattern = r"tiktok\.com"
    profile_url_template = "https://www.tiktok.com/@{id}"

    def matches(self, url: str) -> bool:
        return "tiktok.com" in urllib.parse.urlsplit(url or "").netloc.lower()

    def normalize(self, url: str) -> tuple[str, str]:
        vid = _video_id(url)
        if vid:
            return url, f"tiktok:{vid}"
        clean = url.split("?", 1)[0]
        return url, f"url:{clean}"

    def is_profile_url(self, url: str) -> bool:
        path = urllib.parse.urlsplit(url or "").path
        return path.startswith("/@") and "/video/" not in path

    def resolve_short(self, url: str) -> str:
        host = urllib.parse.urlsplit(url or "").netloc.lower()
        if not any(host.endswith(item) for item in _SHORT_HOSTS):
            return url
        request = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.geturl()

    def extract(self, url: str, cfg: dict) -> dict:
        final = self.resolve_short(url)
        scope, cookies = _scope(final)
        detail = scope.get("webapp.video-detail") or {}
        item = ((detail.get("itemInfo") or {}).get("itemStruct") or {})
        if not item:
            raise PlatformError("TikTok 没解析到作品，可能是私密/地区限制/需要验证")
        vid = str(item.get("id") or _video_id(final))
        author = item.get("author") or {}
        video = item.get("video") or {}
        title = str(item.get("desc") or f"TikTok {vid}").strip()
        images = (((item.get("imagePost") or {}).get("images")) or [])
        assets = []
        cover = ""
        if images:
            for idx, image in enumerate(images):
                urls = ((image.get("imageURL") or {}).get("urlList") or [])
                chosen = next((u for u in urls if u), "")
                if not chosen:
                    continue
                assets.append({"idx": idx, "kind": "image", "url": chosen})
                if not cover:
                    cover = chosen
            kind = "images"
        else:
            play = str(video.get("playAddr") or video.get("downloadAddr") or "")
            if not play:
                raise PlatformError("TikTok 没给播放地址（可能被地区限制）")
            assets.append({"idx": 0, "kind": "video", "url": play})
            raw_cover = video.get("cover") or video.get("originCover") or ""
            if isinstance(raw_cover, dict):
                urls = raw_cover.get("urlList") or []
                cover = next((u for u in urls if u), "")
            else:
                cover = str(raw_cover)
            kind = "video"
        if not assets:
            raise PlatformError("TikTok 作品没有可下载的媒体")
        media_headers = {"Referer": "https://www.tiktok.com/"}
        if cookies:
            media_headers["Cookie"] = cookies
        for asset in assets:
            asset["headers"] = dict(media_headers)
        duration = 0.0
        try:
            duration = float(video.get("duration") or 0)
        except (TypeError, ValueError):
            pass
        stats = item.get("stats") or {}
        return {
            "aweme_id": vid,
            "title": title[:200],
            "author": str(author.get("nickname") or author.get("uniqueId") or ""),
            "author_id": str(author.get("secUid") or author.get("id") or ""),
            "created": int(item.get("createTime") or 0),
            "duration": duration,
            "kind": kind,
            "cover": cover,
            "assets": assets,
            "digg": int(stats.get("diggCount") or 0),
            "comment": int(stats.get("commentCount") or 0),
            "platform": self.name,
            "source_url": final,
            "strategy": "tiktok:page",
        }

    def profile(self, url: str, count: int, cfg: dict) -> dict:
        raise PlatformError("TikTok 主页批量暂不支持；先粘单条视频链接")

    def profile_more(self, token: str, cursor: int, count: int, cfg: dict) -> dict:
        raise PlatformError("TikTok 主页批量暂不支持")

    def item_source_url(self, video_id: str) -> str:
        return f"https://www.tiktok.com/@user/video/{video_id}"

    def media_headers(self, cfg: dict) -> dict:
        return {
            "User-Agent": _UA,
            "Referer": "https://www.tiktok.com/",
            "Accept": "*/*",
        }


