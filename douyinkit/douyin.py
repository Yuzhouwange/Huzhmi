"""抖音解析：分享链接 -> 无水印媒体地址。

流程：
  1. 短链（v.douyin.com）跟随重定向，得到最终 URL
  2. 从 URL 里取 aweme_id
  3. 请求 iesdouyin 分享页，解析 window._ROUTER_DATA
  4. video.play_addr_* 取无水印流；images 取图集

风控页（滑块/验证）会解析不到数据，此时提示用户填 Cookie。
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import threading
import time
import urllib.parse
import urllib.request

from .net import (
    IMAGE_EXT as _IMAGE_EXT,
    MOBILE_UA,
    PlatformError,
    UA,
    VIDEO_EXT as _VIDEO_EXT,
    decode_text,
    guess_ext,
)

__all__ = [
    "ExtractError", "MOBILE_UA", "UA", "decode_text", "extract", "guess_ext",
    "hot_board", "login_state", "logout", "normalize", "resolve_short",
    "start_login",
]

_SHARE_TARGETS = (
    # (URL 模板, UA)。移动端分享页给的字段比桌面端多，优先试。
    ("https://m.douyin.com/share/{kind}/{aweme_id}", MOBILE_UA),
    ("https://www.iesdouyin.com/share/{kind}/{aweme_id}/", MOBILE_UA),
    ("https://www.iesdouyin.com/share/{kind}/{aweme_id}/", UA),
)
_ROUTER_MARKER = "window._ROUTER_DATA"
_AWEME_RE = re.compile(
    r"(?:/video/|/note/|/share/video/|/share/note/|modal_id=)(\d{15,25})"
)


class ExtractError(PlatformError):
    """解析失败，消息直接展示给用户。"""


def normalize(url: str) -> tuple[str, str]:
    """返回 (干净链接, 去重键)。去重键优先用 aweme_id。"""
    url = (url or "").strip()
    m = _AWEME_RE.search(url)
    if m:
        return url, f"aweme:{m.group(1)}"
    bare = urllib.parse.urlsplit(url)
    clean = f"{bare.scheme}://{bare.netloc}{bare.path}"
    return url, f"url:{clean}"


def extract(url: str, cookie: str = "", proxy: str = "", retries: int = 3,
            live_mode: str = "video") -> dict:
    client = _Client(cookie=cookie, proxy=proxy, retries=retries)
    final = client.resolve(url)
    aweme_id = _aweme_id(final) or _aweme_id(url)
    if not aweme_id:
        raise ExtractError("没认出作品 ID：请粘贴抖音作品链接或完整分享文案。")

    client.bootstrap()
    item = None
    last_error = ""
    hit = ""
    from . import rules as rules_mod

    rule_targets = [
        (row["template"], MOBILE_UA if row.get("ua") == "mobile" else UA)
        for row in rules_mod.load()["douyin"]["share_targets"]
    ] or list(_SHARE_TARGETS)
    # 先 note 再 video：实测 note 路由对视频作品同样下发数据，且更少被风控拦
    for kind in ("note", "video"):
        for template, ua in rule_targets:
            page_url = template.format(kind=kind, aweme_id=aweme_id)
            try:
                html, _ = client.get(
                    page_url, referer="https://www.douyin.com/", ua=ua, limit=6_000_000
                )
            except ExtractError as exc:
                last_error = str(exc)
                continue
            item = _parse_item(html)
            if item:
                hit = page_url
                break
            last_error = _diagnose(html)
        if item:
            break

    if not item:
        # 自动降级：分享页全挂时借真实浏览器页面再要一次详情
        from . import config as config_mod

        if (config_mod.load().get("auto_fallback", True)
                and rules_mod.auto_fallback_enabled()):
            try:
                from .bridge import Bridge

                detail = Bridge.get().aweme_detail(aweme_id)
                if detail:
                    item = detail
                    hit = "browser:detail"
            except Exception as exc:  # noqa: BLE001 - 兜底失败要保留原因
                last_error = f"浏览器兜底失败：{exc}"

    if not item:
        hint = (
            "解析不到作品数据，通常是抖音风控。先试试在「设置」里填浏览器登录后的完整 Cookie；"
            "仍失败说明该 IP 被限流或作品已删除。"
        )
        raise ExtractError(f"{hint}{(' 详情：' + last_error) if last_error else ''}")

    meta = _build_meta(item, final, live_mode)
    meta["strategy"] = hit
    return meta


# --------------------------------------------------------------------------
# 网络
# --------------------------------------------------------------------------


class _Client:
    def __init__(self, cookie: str = "", proxy: str = "", retries: int = 3) -> None:
        handlers: list = [urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())]
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        self.opener = urllib.request.build_opener(*handlers)
        self.cookie = (cookie or "").strip()
        self.retries = max(1, retries)

    def _headers(self, referer: str | None = None, ua: str | None = None) -> dict:
        headers = {
            "User-Agent": ua or UA,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if referer:
            headers["Referer"] = referer
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    def get(
        self,
        url: str,
        referer: str | None = None,
        timeout: int = 25,
        limit: int = 8_000_000,
        ua: str | None = None,
    ) -> tuple[bytes, str]:
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                req = urllib.request.Request(url, headers=self._headers(referer, ua))
                with self.opener.open(req, timeout=timeout) as resp:
                    return resp.read(limit), resp.geturl()
            except Exception as exc:  # noqa: BLE001 - 统一转成可读错误
                last = exc
                time.sleep(0.5 * (attempt + 1))
        raise ExtractError(f"网络请求失败：{last}")

    def resolve(self, url: str) -> str:
        """短链跟随重定向。失败就用原链接，后面还能靠 ID 兜底。"""
        if "v.douyin.com" in url:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": MOBILE_UA})
                with self.opener.open(req, timeout=15) as resp:
                    resp.read(1024)
                    return resp.geturl()
            except Exception:  # noqa: BLE001
                return url
        return url

    def bootstrap(self) -> None:
        """没有用户 Cookie 时，先访问首页拿 ttwid 之类的匿名 Cookie。"""
        if self.cookie:
            return
        try:
            req = urllib.request.Request(
                "https://www.douyin.com/",
                headers={"User-Agent": UA, "Accept": "text/html,*/*"},
            )
            with self.opener.open(req, timeout=12) as resp:
                resp.read(2048)
        except Exception:  # noqa: BLE001 - 拿不到就算了
            pass


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------


def _aweme_id(url: str) -> str | None:
    m = _AWEME_RE.search(url or "")
    if m:
        return m.group(1)
    return None


def resolve_short(url: str, timeout: int = 15) -> str:
    """短链跟随重定向，返回最终 URL；失败就原样返回。"""
    if "v.douyin.com" not in (url or ""):
        return url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": MOBILE_UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(1024)
            return resp.geturl()
    except Exception:  # noqa: BLE001
        return url


def _parse_item(html: bytes) -> dict | None:
    """从 HTML 里抠出 _ROUTER_DATA 的 JSON。

    不用正则整体匹配：页面几百 KB，`.*?` 回溯会慢，改用定位 + raw_decode。
    """
    text = decode_text(html)
    pos = text.find(_ROUTER_MARKER)
    if pos < 0:
        return None
    eq = text.find("=", pos)
    if eq < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(text[eq + 1:].lstrip())
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    loader = data.get("loaderData") or {}
    for value in loader.values():
        if not isinstance(value, dict):
            continue
        items = ((value.get("videoInfoRes") or {}).get("item_list")) or []
        if items and isinstance(items[0], dict):
            return items[0]
    return None


def _diagnose(html: bytes) -> str:
    text = decode_text(html)
    if "验证" in text or "captcha" in text.lower() or "verify" in text.lower():
        return "页面要求人机验证"
    if "作品不存在" in text or "已删除" in text:
        return "作品不存在或已删除"
    return f"分享页没有 _ROUTER_DATA（{len(text)} 字节）"


def _build_meta(item: dict, source_url: str, live_mode: str = "video") -> dict:
    aweme_id = str(item.get("aweme_id") or "")
    desc = (item.get("desc") or "").strip()
    author = ((item.get("author") or {}).get("nickname") or "").strip()
    title = desc or f"douyin-{aweme_id}"

    assets: list[dict] = []
    images = item.get("images")
    if not (isinstance(images, list) and images):
        # 有些图集把图片放在 image_post_info 里（老接口 / 不同客户端）
        extra = (item.get("image_post_info") or {}).get("images")
        if isinstance(extra, list) and extra:
            images = extra
    if isinstance(images, list) and images:
        # 实况图（Live Photo）= 一张静态图 + 一段几秒的 mp4。
        # live_mode: "video" 只存视频 / "both" 图+视频 / "image" 只存图
        live_mode = (live_mode or "video").lower()
        has_live = False
        for position, image in enumerate(images):
            image = image or {}
            urls = image.get("url_list") or []
            clip = _live_clip(image)
            if clip and live_mode != "image":
                has_live = True
                assets.append(
                    {
                        "idx": len(assets),
                        "index": position + 1,
                        "kind": "video",
                        "role": "live",
                        "url": clip,
                        "live": True,
                    }
                )
                if live_mode != "both":
                    continue
            if not urls:
                continue
            if clip and live_mode == "both":
                # 图和视频成对，用同一个序号：xxx - 01.jpeg / xxx - 01.mp4
                assets.append(
                    {"idx": len(assets), "index": position + 1,
                     "kind": "image", "url": _pick_image(urls)}
                )
            else:
                assets.append(
                    {"idx": len(assets), "kind": "image", "url": _pick_image(urls)}
                )
        kind = "live" if has_live else "images"
        if has_live and live_mode == "image":
            kind = "images"
    else:
        video = item.get("video") or {}
        url = _pick_video(video)
        if not url:
            raise ExtractError("作品里没有可下载的视频地址。")
        assets.append({"idx": 0, "kind": "video", "url": url})
        kind = "video"

    if not assets:
        raise ExtractError("作品里没有媒体资源。")

    return {
        "aweme_id": aweme_id,
        "title": title,
        "author": author or "unknown",
        "author_id": (item.get("author") or {}).get("sec_uid") or "",
        "kind": kind,
        "assets": assets,
        "source_url": source_url,
        "created": int(item.get("create_time") or 0),
    }


def _live_clip(image: dict) -> str | None:
    """实况图里那段短视频的地址；普通图片没有 video 字段。"""
    video = image.get("video")
    if not isinstance(video, dict):
        return None
    return _pick_video(video)


def _pick_video(video: dict) -> str | None:
    # play_addr 系列通常是无水印流；download_addr 是带水印的，放最后兜底
    for key in ("play_addr_h264", "play_addr_265", "play_addr", "download_addr"):
        urls = (video.get(key) or {}).get("url_list") or []
        for url in urls:
            if not url:
                continue
            # 老接口会返回 playwm，替换成 play 即无水印
            return url.replace("/playwm/", "/play/").replace("playwm", "play")
    return None


# 同一个图有多个地址时怎么挑：jpeg 兼容性最好（Windows 相册/微信都认），
# webp 体积小但很多看图工具不认，所以排在最后。
_IMAGE_PREFERENCE = ((".jpeg", 0), (".jpg", 0), (".png", 1), (".webp", 2))


def _image_rank(url: str) -> int:
    low = (url or "").lower()
    for ext, score in _IMAGE_PREFERENCE:
        if ext in low:
            return score
    return 3


def _pick_image(urls: list[str]) -> str:
    """挑一张图：优先 jpeg，其次 png，最后才是 webp。"""
    usable = [url for url in urls if url]
    if not usable:
        return ""
    return min(usable, key=_image_rank)


# --------------------------------------------------------------------------
# 抖音扫码登录
# --------------------------------------------------------------------------

_LOGIN_LOCK = threading.Lock()
_LOGIN_STATE: dict = {"status": "idle", "message": "", "account": None}
_LOGIN_CANCEL = threading.Event()
# 登录态关键 Cookie；sessionid / sid_guard 是判定登录成功的依据
_LOGIN_COOKIES = (
    "sessionid", "sessionid_ss", "sid_guard", "sid_tt", "uid_tt",
    "uid_tt_ss", "ttwid", "passport_csrf_token", "s_v_web_id", "msToken",
)


def login_state() -> dict:
    with _LOGIN_LOCK:
        return dict(_LOGIN_STATE)


def _set_login_state(status: str, message: str, account: dict | None = None) -> None:
    with _LOGIN_LOCK:
        _LOGIN_STATE.update({"status": status, "message": message, "account": account})


def start_login(timeout: int = 240) -> dict:
    """开有界面的浏览器让用户扫码，登录成功后自动把 Cookie 收进配置。"""
    with _LOGIN_LOCK:
        if _LOGIN_STATE["status"] == "waiting":
            return dict(_LOGIN_STATE)
        _LOGIN_CANCEL.clear()
        _LOGIN_STATE.update(
            {
                "status": "waiting",
                "message": "请在弹出的浏览器窗口里用抖音 App 扫码（没看到登录框就点右上角登录）",
                "account": None,
            }
        )
    threading.Thread(target=_login_worker, args=(timeout,), daemon=True).start()
    return login_state()


def _login_worker(timeout: int) -> None:
    from .cdp import Browser

    browser = None
    try:
        browser = Browser(headless=False)
        browser.open_page("https://www.douyin.com/")
        jar: dict[str, str] = {}
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(1.5)
            if _LOGIN_CANCEL.is_set():
                _set_login_state("idle", "已取消登录")
                return
            try:
                cookies = browser.call("Storage.getCookies", {}).get("cookies", [])
            except Exception:  # noqa: BLE001
                continue
            jar = {
                c["name"]: c["value"]
                for c in cookies
                if "douyin" in (c.get("domain") or "")
            }
            if jar.get("sessionid") or jar.get("sid_guard"):
                break

        if not (jar.get("sessionid") or jar.get("sid_guard")):
            _set_login_state("timeout", "等待超时：没有检测到登录")
            return

        cookie = "; ".join(f"{k}={jar[k]}" for k in _LOGIN_COOKIES if jar.get(k))
        from . import config as config_mod

        config_mod.save({"cookie": cookie})
        _set_login_state(
            "ok", f"已登录抖音（保存了 {len(cookie.split('; '))} 项 Cookie）", None
        )
    except Exception as exc:  # noqa: BLE001
        _set_login_state("error", f"登录失败：{exc}")
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass


def logout() -> None:
    from . import config as config_mod

    _LOGIN_CANCEL.set()
    config_mod.save({"cookie": ""})
    _set_login_state("idle", "已退出登录")


_HOT_URL = "https://www.douyin.com/aweme/v1/web/hot/search/list/"
_HOT_PARAMS = {
    "device_platform": "webapp",
    "aid": "6383",
    "channel": "channel_pc_web",
    "pc_client_type": "1",
    "version_code": "290100",
    "version_name": "29.1.0",
    "cookie_enabled": "true",
    "screen_width": "1536",
    "screen_height": "864",
    "browser_language": "zh-CN",
    "browser_platform": "Win32",
    "browser_name": "Chrome",
    "browser_version": "139.0.0.0",
    "browser_online": "true",
    "engine_name": "Blink",
    "engine_version": "139.0.0.0",
    "os_name": "Windows",
    "os_version": "10",
    "cpu_core_num": "16",
    "device_memory": "8",
    "platform": "PC",
    "downlink": "10",
    "effective_type": "4g",
    "round_trip_time": "200",
}


def hot_board(limit: int = 50, cookie: str = "") -> list[dict]:
    """抖音热榜，不需要签名；返回 word / 热度 / sentence_id。"""
    url = f"{_HOT_URL}?{urllib.parse.urlencode(_HOT_PARAMS)}"
    headers = {"User-Agent": UA, "Referer": "https://www.douyin.com/",
               "Accept": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(decode_text(response.read()))
    except Exception as exc:  # noqa: BLE001
        raise ExtractError(f"热榜请求失败：{exc}") from exc
    words = ((data.get("data") or {}).get("word_list") or [])[:max(0, int(limit))]
    result = []
    for row in words:
        cover = ""
        for item in (row.get("word_cover") or {}).get("url_list") or []:
            if item:
                cover = item
                break
        result.append({
            "word": row.get("word") or "",
            "hot_value": int(row.get("hot_value") or 0),
            "sentence_id": str(row.get("sentence_id") or ""),
            "cover": cover,
        })
    return result
