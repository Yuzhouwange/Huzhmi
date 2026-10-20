"""页面桥：在真实浏览器页面里发抖音 API 请求。

为什么要绕这一层：抖音的 `aweme/v1/web/*` 接口除了 a_bogus，还校验
`x-secsdk-web-signature`（Argus SDK）和 uifid，这两样只有页面 JS 算得出来。
把 fetch 放回页面上下文，签名由页面自己补，我们只取 JSON。
"""

from __future__ import annotations

import json
import re
import threading
import time
from urllib.parse import quote, urlencode

from .cdp import Browser, CDPError

_POST_PATH = "/aweme/v1/web/aweme/post/"
_SEARCH_PATH = "/aweme/v1/web/general/search/single/"
_USER_RE = re.compile(r"/(?:user|share/user)/([A-Za-z0-9_-]{20,})")

# 抖音只给未登录用户看第一页（实测 count 拉大也停在 40 条左右）；
# 想要完整的作品列表必须复用登录 Cookie。
_PROFILE_PAGE = "https://www.douyin.com/user/%s"
_LOGIN_WALL = "登录后查看更多作品"
_WORKS_RE = re.compile(r"作品\s*([\d,]+)")

_BASE_PARAMS = {
    "device_platform": "webapp",
    "aid": "6383",
    "channel": "channel_pc_web",
    "update_version_code": "170400",
    "pc_client_type": "1",
    "pc_libra_divert": "Windows",
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
    "support_h265": "1",
    "support_dash": "1",
}


def is_profile_url(url: str) -> bool:
    return bool(_USER_RE.search(url or "")) or "/user/" in (url or "")


def parse_sec_uid(url: str) -> str | None:
    match = _USER_RE.search(url or "")
    return match.group(1) if match else None


def parse_profile_text(text: str) -> dict:
    """从主页正文里读「作品总数」和「是否需要登录」。

    页面在未登录时会渲染「登录后查看更多作品」，并且只给出第一页作品；
    把这两点读出来，界面才能解释「为什么只看到二十几条」。
    """
    text = text or ""
    total = 0
    for match in _WORKS_RE.finditer(text):
        try:
            total = int(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if total > 0:
            break
    return {"total_works": total, "needs_login": _LOGIN_WALL in text}


_FETCH_JS = """
(async () => {
  try {
    const resp = await fetch(%(path)s, {credentials: 'include', headers: {'Accept': 'application/json'}});
    const text = await resp.text();
    if (!text) return JSON.stringify({status: resp.status, error: 'empty body'});
    let data;
    try { data = JSON.parse(text); }
    catch (e) { return JSON.stringify({status: resp.status, error: 'not json', head: text.slice(0, 200)}); }
    // 一张图有多个地址：jpeg 最通用（Windows 相册/微信都认），webp 排最后
    const pickImage = (urls) => {
      const rank = (u) => {
        const low = String(u || '').toLowerCase();
        if (low.includes('.jpeg') || low.includes('.jpg')) return 0;
        if (low.includes('.png')) return 1;
        if (low.includes('.webp')) return 2;
        return 3;
      };
      const usable = (urls || []).filter(Boolean);
      if (!usable.length) return null;
      return usable.slice().sort((a, b) => rank(a) - rank(b))[0];
    };
    const norm = (it) => {
      const v = it.video || {};
      const cover = pickImage(((v.cover || {}).url_list || []))
                 || ((v.origin_cover || {}).url_list || [])[0]
                 || pickImage((((it.images || [])[0] || {}).url_list) || [])
                 || null;
      // 图集作品的 video 是自动生成的「图文 + BGM」视频，不能当作品本体
      const play = (it.images && it.images.length) ? null
        : ((((v.play_addr || {}).url_list) || [])[0] || null);
      return {
        aweme_id: it.aweme_id,
        desc: it.desc || '',
        create_time: it.create_time || 0,
        duration: v.duration || 0,
        cover: cover,
        author: (it.author || {}).nickname || '',
        sec_uid: (it.author || {}).sec_uid || '',
        kind: (it.is_live_photo || (it.images || []).some(im => im && im.video))
          ? 'live' : ((it.images && it.images.length) ? 'images' : 'video'),
        is_live_photo: !!it.is_live_photo,
        live: (it.is_live_photo ? (it.images || []) : [])
          .map(im => pickImage(((im || {}).video || {}).play_addr
            ? (((im || {}).video || {}).play_addr.url_list || [])
            : []))
          .filter(Boolean),
        play_url: play,
        images: (it.images || []).map(im => pickImage(im.url_list)).filter(Boolean),
        digg: ((it.statistics || {}).digg_count) || 0,
        comment: ((it.statistics || {}).comment_count) || 0,
      };
    };
    return JSON.stringify({
      status: resp.status,
      status_code: data.status_code,
      has_more: data.has_more,
      max_cursor: data.max_cursor || data.cursor,
      items: (data.aweme_list
        || (Array.isArray(data.data)
            ? data.data.map(x => x && x.aweme_info).filter(Boolean)
            : [])
      ).map(norm),
      filter: data.filter_detail || null
    });
  } catch (e) {
    return JSON.stringify({status: 0, error: String(e)});
  }
})()
"""

_RESOLVE_JS = """
(() => {
  const m = location.pathname.match(/\\/user\\/([A-Za-z0-9_-]{20,})/);
  return JSON.stringify({
    href: location.href,
    sec_uid: m ? m[1] : null,
    title: document.title || ''
  });
})()
"""


_DETAIL_JS = """
(async () => {
  const url = "https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id="
    + encodeURIComponent(%(id)s)
    + "&device_platform=webapp&aid=6383&channel=channel_pc_web"
    + "&pc_client_type=1&version_code=190500&version_name=19.5.0"
    + "&cookie_enabled=true&platform=PC&downlink=10";
  const resp = await fetch(url, {credentials: 'include',
                                 headers: {'accept': 'application/json'}});
  const text = await resp.text();
  let data = null;
  try { data = JSON.parse(text); }
  catch (e) { return JSON.stringify({status: resp.status, bad: text.slice(0, 120)}); }
  return JSON.stringify({status: resp.status,
                         status_code: data.status_code,
                         aweme_detail: data.aweme_detail || null});
})()
"""


class BridgeError(RuntimeError):
    pass


class Bridge:
    """全局唯一的页面桥，懒启动、复用浏览器。"""

    _instance: "Bridge | None" = None
    _instance_lock = threading.Lock()

    @classmethod
    def get(cls) -> "Bridge":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = Bridge()
            return cls._instance

    @classmethod
    def shutdown(cls) -> None:
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance.close()
                cls._instance = None

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._browser: Browser | None = None
        self._ready = False
        self._cookie_sig = ""
        self._warmed: str | None = None
        self.last_error = ""

    # ---------- 生命周期 ----------

    def _ensure(self) -> Browser:
        if self._browser is not None:
            self._apply_cookie(self._browser)
            return self._browser
        last_exc: Exception | None = None
        for _ in range(2):
            browser = None
            try:
                browser = Browser(headless=True)
                browser.open_page("about:blank")
                # 首页先走一遍，让页面装上签名拦截器、拿到匿名 Cookie
                browser.goto("https://www.douyin.com/", wait_pages=1.2)
                self._browser = browser
                self._ready = True
                self._apply_cookie(browser)
                return browser
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:  # noqa: BLE001
                        pass
                time.sleep(1.0)
        assert last_exc is not None
        self.last_error = f"{type(last_exc).__name__}: {last_exc}"
        raise BridgeError(f"页面桥启动失败：{last_exc}") from last_exc

    def _apply_cookie(self, browser: Browser) -> None:
        """把设置里保存的抖音 Cookie 写进浏览器。

        没有这一步，主页列表永远是「未登录视角」：抖音只给第一页，
        用户扫码登录了也还是只看到二十几条。
        """
        from . import config as config_mod

        cookie = (config_mod.load().get("cookie") or "").strip()
        if cookie == self._cookie_sig:
            return
        if cookie:
            try:
                browser.set_cookies(cookie)
            except CDPError as exc:
                self.last_error = f"写入登录 Cookie 失败：{exc}"
        self._cookie_sig = cookie
        self._warmed = None        # 登录态变了，页面环境要重新预热

    def close(self) -> None:
        with self._lock:
            if self._browser is not None:
                try:
                    self._browser.close()
                except Exception:  # noqa: BLE001
                    pass
                self._browser = None
                self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    # ---------- 能力 ----------

    def _warm(self, browser: Browser, sec_uid: str) -> str:
        """在主页上待一会儿，让页面装好签名环境（uifid 等）。

        不做这一步直接 fetch，接口会回 `403 Blocked by ArgusSecurityPlugin
        Uifid Not Found`；`_profile_more` 换号时也会踩到。返回页面正文，
        顺手给界面读「作品总数 / 是否需要登录」。
        """
        if self._warmed != sec_uid:
            browser.goto(_PROFILE_PAGE % sec_uid, wait_pages=2.5)
            self._warmed = sec_uid
        try:
            return browser.eval("document.body.innerText") or ""
        except CDPError:
            return ""

    def resolve_profile(self, url: str) -> dict:
        """把主页链接（含短链）解析成 sec_uid + 昵称 + 作品总数。"""
        with self._lock:
            browser = self._ensure()
            sec_uid = parse_sec_uid(url)
            if sec_uid and "v.douyin.com" not in url:
                browser.goto(_PROFILE_PAGE % sec_uid, wait_pages=0.8)
            else:
                # 短链或分享文案：让浏览器跟随跳转
                browser.goto(url, wait_pages=2.0)

            # 标题是异步渲染的，轮询等一会儿；拿不到就用作品作者的昵称兜底
            data: dict = {}
            deadline = time.time() + 8
            while time.time() < deadline:
                data = json.loads(browser.eval(_RESOLVE_JS) or "{}")
                if data.get("title") and "抖音" in data["title"]:
                    break
                time.sleep(0.3)

            sec_uid = data.get("sec_uid") or sec_uid
            if not sec_uid:
                raise BridgeError("这不是一个能识别的主页链接（没找到 sec_uid）。")
            self._warmed = sec_uid
            # 作品总数先出现、作品网格和「登录后查看更多作品」后出现（约 2s），
            # 所以等到正文长度稳定或者看到登录墙为止，别提前跳出。
            text = ""
            deadline = time.time() + 5
            last_len = -1
            stable = 0
            while time.time() < deadline:
                try:
                    text = browser.eval("document.body.innerText") or ""
                except CDPError:
                    text = ""
                meta = parse_profile_text(text)
                if meta["needs_login"]:
                    break
                if len(text) == last_len:
                    stable += 1
                    if stable >= 2 and meta["total_works"]:
                        break
                else:
                    stable = 0
                    last_len = len(text)
                time.sleep(0.3)
            meta = parse_profile_text(text)
            title = (data.get("title") or "").strip()
            nickname = re.split(r"的抖音|[-_]抖音", title)[0].strip() if title else ""
            return {
                "sec_uid": sec_uid,
                "nickname": nickname,
                "href": data.get("href", ""),
                **meta,
            }

    def user_posts(self, sec_uid: str, cursor: int = 0, count: int = 50) -> dict:
        with self._lock:
            browser = self._ensure()
            self._warm(browser, sec_uid)
            params = dict(_BASE_PARAMS)
            params.update(
                {
                    "sec_user_id": sec_uid,
                    "max_cursor": str(cursor),
                    "count": str(count),
                    "locate_query": "false",
                    "show_live_replay_strategy": "1",
                    "need_time_list": "1",
                    "time_list_query": "0",
                    "whale_cut_token": "",
                    "cut_version": "1",
                    "publish_video_strategy_type": "2",
                }
            )
            path = f"{_POST_PATH}?{urlencode(params)}"
            data = self._request(browser, path)
            if int(data.get("status") or 0) != 200:
                # 页面环境过期（uifid / 签名失效）：重新预热再试一次
                self._warmed = None
                self._warm(browser, sec_uid)
                data = self._request(browser, path)

            status = int(data.get("status") or 0)
            if status != 200:
                raise BridgeError(
                    f"接口返回 {status}：{data.get('error') or data.get('head') or '未知错误'}"
                )
            if data.get("status_code") not in (0, None):
                raise BridgeError(f"抖音返回 status_code={data.get('status_code')}")

            items = data.get("items") or []
            if not items and cursor == 0:
                # 新号或全部私密；再兜一层：DOM 里有的链接也算
                items = self._dom_posts(browser)
            # 有下一页标记、但下一页是空的：抖音在没登录时就是这么卡的
            needs_login = bool(cursor) and not items
            return {
                "items": items,
                "has_more": bool(data.get("has_more")) and not needs_login,
                "max_cursor": int(data.get("max_cursor") or 0),
                "needs_login": needs_login,
            }

    def search(self, keyword: str, offset: int = 0, count: int = 20) -> dict:
        """关键词搜索作品；结果结构和主页列表一致，前端可复用多选网格。"""
        keyword = str(keyword or "").strip()
        if not keyword:
            raise BridgeError("搜索词为空")
        with self._lock:
            browser = self._ensure()
            try:
                browser.goto("https://www.douyin.com/search/" + quote(keyword),
                             wait_pages=2.0)
                self._warmed = None
            except Exception:  # noqa: BLE001 - 打不开也继续试接口
                pass
            params = dict(_BASE_PARAMS)
            params.update({
                "keyword": keyword,
                "offset": str(max(0, int(offset))),
                "count": str(max(1, min(50, int(count)))),
                "search_channel": "aweme_general",
                "sort_type": "0",
                "publish_time": "0",
                "search_source": "normal_search",
                "query_correct_type": "1",
                "is_filter_search": "0",
                "from_group_id": "",
            })
            path = f"{_SEARCH_PATH}?{urlencode(params)}"
            data = self._request(browser, path)
            status = int(data.get("status") or 0)
            if status != 200:
                raise BridgeError(
                    f"搜索接口返回 {status}：{data.get('error') or data.get('head') or '可能是风控'}"
                )
            if data.get("status_code") not in (0, None):
                raise BridgeError(
                    str(data.get("status_msg") or
                        f"搜索接口 status_code={data.get('status_code')}（多数情况是没登录）")
                )
            items = data.get("items") or []
            return {
                "items": items,
                "has_more": bool(data.get("has_more")),
                "cursor": int(data.get("max_cursor") or 0),
                "keyword": keyword,
            }

    def aweme_detail(self, aweme_id: str) -> dict:
        """取单个作品的详情（原始结构）。

        分享页的 `_ROUTER_DATA` 里没有实况图的短视频，只有详情接口才有
        `images[].video`，所以「实况」这条路必须借页面会话再问一次。
        """
        if not aweme_id:
            return {}
        browser = self._ensure()
        self._apply_cookie(browser)
        # 详情接口认页面上下文：先打开这条作品的网页，拿到 uifid 等 cookie 再问
        try:
            browser.goto(f"https://www.douyin.com/video/{aweme_id}",
                         wait_pages=1.5)
        except Exception:  # noqa: BLE001 - 打不开也继续试一次
            pass
        raw = browser.eval(_DETAIL_JS % {"id": json.dumps(str(aweme_id))},
                           await_promise=True, timeout=90)
        data = json.loads(raw or "{}")
        if int(data.get("status") or 0) != 200:
            raise BridgeError(f"详情接口返回 {data.get('status')}")
        return data.get("aweme_detail") or {}

    def _request(self, browser: Browser, path: str) -> dict:
        js = _FETCH_JS % {"path": json.dumps(path)}
        raw = browser.eval(js, timeout=120)
        return json.loads(raw or "{}")

    def _dom_posts(self, browser: Browser) -> list[dict]:
        """兜底：直接从渲染后的 DOM 里取作品链接。"""
        js = """
        Array.from(new Set(Array.from(document.querySelectorAll('a[href*="/video/"]'))
          .map(a => (a.href.match(/\\/video\\/(\\d+)/) || [])[1]).filter(Boolean)))
        """
        try:
            ids = browser.eval(js) or []
        except CDPError:
            return []
        return [
            {
                "aweme_id": aweme_id,
                "desc": "",
                "create_time": 0,
                "duration": 0,
                "cover": None,
                "author": "",
                "sec_uid": "",
                "kind": "video",
                "play_url": None,
                "images": [],
                "digg": 0,
                "comment": 0,
            }
            for aweme_id in ids
        ]


def quick_check() -> dict:
    """自检：返回桥状态，方便 /api/health 展示。"""
    bridge = Bridge.get()
    return {"ready": bridge.ready, "error": bridge.last_error}
