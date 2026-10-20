"""网易云扫码登录：拿 unikey → 前端画二维码 → 轮询 → 收 MUSIC_U。

只用公开的登录接口，不碰账号密码：

    GET /api/login/qrcode/unikey?type=1          -> unikey
    GET /api/login/qrcode/client/login?key=&type=1
        800 过期 / 801 等待 / 802 已扫描待确认 / 803 成功

803 的响应会 `Set-Cookie: MUSIC_U=...`，用 CookieJar 收下来拼成 Cookie 字符串，
交给 `config.music_cookie_netease`。之后 `providers.NeteaseProvider` 会带着它去
请求 320k / 无损 / 会员曲的直链。
"""

from __future__ import annotations

import http.cookiejar
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
API = "https://music.163.com"
LOGIN_URL = "https://music.163.com/login?codekey={key}"
_CODES = {
    800: "二维码已过期，请刷新",
    801: "等待扫码",
    802: "已扫描，请在手机上确认",
    803: "登录成功",
}


class LoginError(RuntimeError):
    pass


def _cookie_header(jar: http.cookiejar.CookieJar) -> str:
    return "; ".join(f"{cookie.name}={cookie.value}" for cookie in jar)


class NeteaseLogin:
    """一个进程内只保留最近一次扫码会话。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._key = ""
        self._created = 0.0
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar))
        self._account_cache: tuple[float, dict] | None = None

    # ------------------------------------------------------------------ 扫码

    def start(self) -> dict:
        with self._lock:
            self._jar.clear()
            self._opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(self._jar))
            data = self._get_json(f"{API}/api/login/qrcode/unikey?type=1")
            key = str(data.get("unikey") or "")
            if not key:
                raise LoginError(data.get("message") or "网易云没有返回二维码 key")
            self._key = key
            self._created = time.time()
            return {"key": key, "qr": LOGIN_URL.format(key=key),
                    "provider": "netease", "expires": 300}

    def poll(self, key: str | None = None) -> dict:
        with self._lock:
            key = str(key or self._key or "")
            if not key:
                raise LoginError("请先点「扫码登录」")
            data = self._get_json(
                f"{API}/api/login/qrcode/client/login?key={urllib.parse.quote(key)}&type=1")
            code = int(data.get("code") or 0)
            result = {
                "code": code,
                "message": _CODES.get(code) or data.get("message") or "",
                "nickname": data.get("nickname") or "",
                "avatar": data.get("avatarUrl") or "",
                "provider": "netease",
            }
            if code == 803:
                cookie = _cookie_header(self._jar)
                if "MUSIC_U=" not in cookie:
                    raise LoginError("登录成功但没拿到 MUSIC_U，请重试")
                result["cookie"] = cookie
                self._key = ""
                self._account_cache = None
            return result

    # ------------------------------------------------------------------ 账号

    def account(self, cookie: str, *, use_cache: bool = True) -> dict:
        cookie = str(cookie or "").strip()
        if not cookie:
            return {"logged_in": False, "nickname": "", "vip": False,
                    "provider": "netease"}
        now = time.time()
        if use_cache and self._account_cache and now - self._account_cache[0] < 60:
            cached = dict(self._account_cache[1])
            cached["cached"] = True
            return cached
        try:
            data = self._get_json(f"{API}/api/nuser/account/get",
                                  headers={"Cookie": cookie},
                                  use_jar=False)
        except (LoginError, urllib.error.URLError, OSError):
            result = {"logged_in": bool(cookie), "nickname": "", "vip": False,
                      "provider": "netease", "error": "查询账号失败"}
            self._account_cache = (now, dict(result))
            return result
        profile = data.get("profile") or {}
        vip_type = int(profile.get("vipType") or 0)
        result = {
            "logged_in": bool(profile.get("userId")),
            "nickname": profile.get("nickname") or "",
            "avatar": profile.get("avatarUrl") or "",
            "vip": vip_type > 0,
            "vip_type": vip_type,
            "provider": "netease",
        }
        self._account_cache = (now, dict(result))
        return result
    # ------------------------------------------------------------------ 工具

    def _get_json(self, url: str, headers: dict | None = None,
                  use_jar: bool = True) -> dict:
        merged = {"User-Agent": UA, "Referer": f"{API}/"}
        merged.update(headers or {})
        request = urllib.request.Request(url, headers=merged)
        opener = self._opener if use_jar else urllib.request.build_opener()
        try:
            with opener.open(request, timeout=20) as response:
                raw = response.read()
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            raise LoginError(f"请求网易云失败：{exc}") from exc
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError as exc:
            raise LoginError("网易云返回的不是 JSON") from exc
        if not isinstance(data, dict):
            raise LoginError("网易云返回格式异常")
        return data


__all__ = ["LoginError", "NeteaseLogin"]
