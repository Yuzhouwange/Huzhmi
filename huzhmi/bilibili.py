"""B 站解析：BV / av / b23.tv 短链 -> playurl。

范围说明：
  - 只处理**公开视频**（UGC）。大会员 / 付费番剧 / 充电专属不做绕过。
  - 默认走 DASH（音视频分离），能拿到 1080P / 4K / HDR；两路 `.m4s` 由
    `mp4mux` 无损合成一个普通 MP4，不依赖 ffmpeg。
  - DASH 不可用时退回 `fnval=1` 的整段 MP4（最高 720P）。
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import re
import threading
import time
import urllib.parse
import urllib.request

from .net import UA, PlatformError, decode_text

__all__ = [
    "BiliError", "UA", "extract", "is_bilibili", "is_space_url", "login_state",
    "logout", "normalize", "parse_mid", "resolve_short", "start_login",
    "user_videos",
]

_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_AV_RE = re.compile(r"(?:^|/)av(\d+)", re.I)
_P_RE = re.compile(r"[?&]p=(\d+)")
# UP 主主页有几种写法，手机分享出来的常常不是 space.bilibili.com：
#   https://space.bilibili.com/123456
#   https://m.bilibili.com/space/123456      （App / 手机分享）
#   https://www.bilibili.com/space/123456    （改版后的 Web 路径）
#   https://space.bilibili.com/123456/video  （后面挂 tab 也算）
_SPACE_RE = re.compile(
    r"(?:space\.bilibili\.com|bilibili\.com/space)/(\d{3,})", re.I
)
_SHORT_HOSTS = ("b23.tv", "bili2233.cn")

# 番剧 / 影视：/bangumi/play/ss<季度id>、/bangumi/play/ep<单集id>
_BANGUMI_RE = re.compile(r"/bangumi/play/(ss|ep)(\d+)", re.I)
_BANGUMI_MEDIA_RE = re.compile(r"/bangumi/media/md(\d+)", re.I)

_VIEW = "https://api.bilibili.com/x/web-interface/view"
_ARCHIVE_STAT = "https://api.bilibili.com/x/web-interface/archive/stat"
_PLAYURL = "https://api.bilibili.com/x/player/playurl"
_PGC_SEASON = "https://api.bilibili.com/pgc/view/web/season"
_PGC_PLAYURL = "https://api.bilibili.com/pgc/player/web/playurl"
_SPACE_ARC = "https://api.bilibili.com/x/space/wbi/arc/search"
_NAV = "https://api.bilibili.com/x/web-interface/nav"
_LOGIN_PAGE = "https://passport.bilibili.com/login"

# 画质从高到低尝试；没登录时平台会自动降到可用档位
_QN_ORDER = (120, 116, 112, 80, 74, 64, 32, 16)

# WBI 混淆表（B 站前端写死的常量）
_MIXIN = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
)

# 从高到低尝试；无登录时平台会自动降到当前账号可用档位
_CODE_HINTS = {
    -400: "请求参数不对",
    # B 站把「稿件被删 / 设为私密 / 无权限」都塞进 -403，光看这个码会误判成
    # 「要登录」。真正的判定交给 _archive_missing() 去查 archive/stat。
    -403: "没有访问权限（可能需要登录、大会员，或视频已删除/私密）",
    -404: "视频不存在或已删除",
    62002: "稿件不可见",
    62004: "稿件审核中",
    87007: "充电专属视频，需要授权",
    87008: "该视频需要大会员",
}

_MISSING_MESSAGE = (
    "视频不存在了：可能已被 UP 主删除，或改成了私密 / 仅粉丝可见"
    "（B 站对这类情况返回 -403「访问权限不足」，并不代表要登录）。"
)

# 番剧接口的错误码（和普通稿件不是一套）
_PGC_HINTS = {
    -400: "番剧接口参数不对，链接可能不完整",
    -404: "找不到这部番剧，可能已下架",
    -10403: "该番剧在当前地区不可看（B 站地区限制）",
    87007: "这一集需要充电 / 大会员，当前账号没有权限",
    87008: "这一集需要大会员，当前账号没有权限",
}


class BiliError(PlatformError):
    """解析失败，消息直接展示给用户。"""


def is_bilibili(url: str) -> bool:
    host = urllib.parse.urlsplit(url or "").netloc.lower()
    return "bilibili.com" in host or any(h in host for h in _SHORT_HOSTS)


def is_space_url(url: str) -> bool:
    """UP 主个人空间链接。"""
    return bool(_SPACE_RE.search(url or ""))


def is_bangumi(url: str) -> bool:
    """番剧 / 影视链接（/bangumi/play/ss… 或 ep…）。"""
    return bool(_BANGUMI_RE.search(url or ""))


def parse_bangumi(url: str) -> tuple[str, int] | None:
    """返回 ("ep"|"ss", 数字 ID)。"""
    match = _BANGUMI_RE.search(url or "")
    if not match:
        return None
    return match.group(1).lower(), int(match.group(2))


def parse_mid(url: str) -> str | None:
    match = _SPACE_RE.search(url or "")
    return match.group(1) if match else None


def normalize(url: str) -> tuple[str, str]:
    url = (url or "").strip()
    bangumi = parse_bangumi(url)
    if bangumi:
        kind, ident = bangumi
        return url, f"bili:{kind}{ident}"
    match = _BV_RE.search(url)
    if match:
        page = _P_RE.search(url)
        suffix = f":p{page.group(1)}" if page else ""
        return url, f"bili:{match.group(1)}{suffix}"
    match = _AV_RE.search(url)
    if match:
        return url, f"bili:av{match.group(1)}"
    bare = urllib.parse.urlsplit(url)
    return url, f"url:{bare.scheme}://{bare.netloc}{bare.path}"


def _headers(cookie: str = "") -> dict:
    headers = {
        "User-Agent": UA,
        "Referer": "https://www.bilibili.com/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _get_json(url: str, cookie: str = "", proxy: str = "", retries: int = 3) -> dict:
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    last: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            req = urllib.request.Request(url, headers=_headers(cookie))
            with opener.open(req, timeout=25) as resp:
                return json.loads(decode_text(resp.read(4_000_000)))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.5 * (attempt + 1))
    raise BiliError(f"请求 B 站接口失败：{last}")


def _resolve_short(url: str) -> str:
    if not any(h in url.lower() for h in _SHORT_HOSTS):
        return url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.geturl()
    except Exception:  # noqa: BLE001
        return url


def resolve_short(url: str) -> str:
    """公开入口：b23.tv 短链 -> 真实地址。"""
    return _resolve_short(url)


def _page_index(url: str) -> int:
    match = _P_RE.search(url)
    try:
        return max(1, int(match.group(1))) if match else 1
    except (TypeError, ValueError):
        return 1


def _archive_missing(bvid: str | None = None, aid: str | None = None,
                     cookie: str = "", proxy: str = "", retries: int = 2) -> bool:
    """稿件是不是已经不在了？

    `view` 对「已删除 / 私密 / 无权限」一律回 -403「访问权限不足」，措辞很像
    「需要登录」。`archive/stat` 则诚实得多：稿件没了直接 HTTP 404。

    返回 True = 确认不存在；False = 稿件仍在（那才可能是权限问题）。
    """
    query = f"bvid={bvid}" if bvid else (f"aid={aid}" if aid else "")
    if not query:
        return False
    try:
        _get_json(f"{_ARCHIVE_STAT}?{query}", cookie, proxy, retries)
    except BiliError as exc:
        return "404" in str(exc)
    except Exception:  # noqa: BLE001 - 探测失败不能反过来影响主流程
        return False
    return False


def _resolve_view(url: str, cookie: str = "", proxy: str = "", retries: int = 3
                  ) -> tuple[dict, str, str, str]:
    """取稿件 view 数据：返回 (data, bvid, aid, 展开后的链接)。

    `extract` 和 `episodes` 共用同一条取数 + 报错逻辑，避免两处判定走偏。
    """
    final = _resolve_short(url)
    bv = _BV_RE.search(final)
    av = _AV_RE.search(final)
    if not bv and not av:
        raise BiliError("没认出 B 站视频号：请粘贴 BV/av 链接或 b23.tv 短链。")

    view_url = f"{_VIEW}?bvid={bv.group(1)}" if bv else f"{_VIEW}?aid={av.group(1)}"
    view = _get_json(view_url, cookie, proxy, retries)
    code = int(view.get("code") or 0)
    if code != 0:
        if code == -403 and _archive_missing(
            bvid=bv.group(1) if bv else None,
            aid=av.group(1) if av else None,
            cookie=cookie, proxy=proxy, retries=min(2, max(1, retries)),
        ):
            # 稿件没了。此时让人去登录只会白折腾：登录了也还是 -403。
            raise BiliError(_MISSING_MESSAGE)
        raise BiliError(_CODE_HINTS.get(code, f"B 站返回 code={code}：{view.get('message')}"))

    data = view["data"]
    return (
        data,
        data.get("bvid") or (bv.group(1) if bv else ""),
        (av.group(1) if av else ""),
        final,
    )


def _season_episodes(season: dict) -> list[dict]:
    """合集（ugc_season）里的每一集。注意 `ep["page"]` 是个对象而不是整数。"""
    items: list[dict] = []
    for section in season.get("sections") or []:
        for ep in section.get("episodes") or []:
            bvid = (ep.get("bvid") or "").strip()
            if not bvid:
                continue
            page_info = ep.get("page") or {}
            page_no = 1
            duration = 0
            if isinstance(page_info, dict):
                page_no = int(page_info.get("page") or 1)
                duration = int(page_info.get("duration") or 0)
            elif isinstance(page_info, int):
                page_no = page_info
            duration = duration or int((ep.get("arc") or {}).get("duration") or 0)
            suffix = f"?p={page_no}" if page_no > 1 else ""
            items.append(
                {
                    "index": len(items) + 1,
                    "label": (ep.get("title") or "").strip() or bvid,
                    "duration": duration,
                    "url": f"https://www.bilibili.com/video/{bvid}{suffix}",
                    "key": f"bili:{bvid}" + (f":p{page_no}" if page_no > 1 else ""),
                }
            )
    return items


def _as_int(value, default: int = 0) -> int:
    """番剧接口里时间/时长偶尔是字符串或日期文本，转不动就给默认值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _pgc_episodes(season: dict) -> list[dict]:
    """番剧的全部集数（正片在 `episodes`，有些季度还会放在 `sections`）。

    老番的字段很不规整：`sections` 可能是 null、列表项可能是字符串、
    单集里 `title` / `duration` 也可能缺失，所以每一步都做类型判断。
    """
    raw: list = list(season.get("episodes") or [])
    sections = season.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if isinstance(section, dict):
                raw.extend(section.get("episodes") or [])

    items: list[dict] = []
    seen: set[int] = set()
    for ep in raw:
        if not isinstance(ep, dict):
            continue
        ep_id = _as_int(ep.get("id"))
        if not ep_id or ep_id in seen:
            continue
        seen.add(ep_id)
        number = str(ep.get("title") or "").strip()
        label = str(ep.get("long_title") or "").strip()
        if number and label:
            label = f"{number} {label}"
        label = label or number or f"第{len(items) + 1}话"
        items.append(
            {
                "index": len(items) + 1,
                "label": label,
                # 番剧给的是毫秒；有的老番直接给 0
                "duration": _as_int(_as_int(ep.get("duration")) / 1000),
                "url": f"https://www.bilibili.com/bangumi/play/ep{ep_id}",
                "key": f"bili:ep{ep_id}",
                "ep_id": ep_id,
            }
        )
    return items


def _season(kind: str, ident: int, cookie: str = "", proxy: str = "",
            retries: int = 3) -> dict:
    """取番剧季度信息（含全部集数）。`kind` 是 "ss" 或 "ep"。"""
    param = "ep_id" if kind == "ep" else "season_id"
    payload = _get_json(f"{_PGC_SEASON}?{param}={ident}", cookie, proxy, retries)
    code = int(payload.get("code") or 0)
    if code != 0:
        raise BiliError(
            _PGC_HINTS.get(code, f"番剧接口返回 code={code}：{payload.get('message')}")
        )
    result = payload.get("result") or payload.get("data")
    if not isinstance(result, dict) or not result:
        raise BiliError("读不到番剧信息：可能链接不对，或这部番已下架。")
    return result


def _episodes_bangumi(kind: str, ident: int, cookie: str = "",
                      proxy: str = "", retries: int = 3) -> dict:
    season = _season(kind, ident, cookie, proxy, retries)
    items = _pgc_episodes(season)
    current = 1
    if kind == "ep":
        for index, ep in enumerate(items, start=1):
            if ep["ep_id"] == ident:
                current = index
                break
    return {
        "platform": "bilibili",
        "title": (season.get("title") or "").strip(),
        "author": _bangumi_author(season),
        "source": "bangumi",
        "has_multi": len(items) > 1,
        "current": current,
        "episodes": items,
    }


def _bangumi_author(season: dict) -> str:
    """番剧的「作者」。

    字段很不规整：新番有 `up_info.uname`，老番 `up_info` 是 null、
    `staff` 可能是一整段纯文本（而不是 [{name: ...}]）。
    """
    up_info = season.get("up_info")
    if isinstance(up_info, dict):
        name = str(up_info.get("uname") or "").strip()
        if name:
            return name

    staff = season.get("staff")
    if isinstance(staff, dict):
        staff = [staff]
    if isinstance(staff, list):
        for entry in staff:
            if isinstance(entry, dict):
                name = str(entry.get("name") or "").strip()
                if name:
                    return name
    return "bilibili 番剧"


def episodes(url: str, cookie: str = "", proxy: str = "", retries: int = 3) -> dict:
    """列出这条链接的「选集」：番剧集数 / 分 P / 合集。

    返回结构给前端画勾选框用；`has_multi` 为 False 时调用方按普通单条处理。
    """
    bangumi = parse_bangumi(url)
    if bangumi:
        return _episodes_bangumi(bangumi[0], bangumi[1], cookie, proxy, retries)

    if _BANGUMI_MEDIA_RE.search(url or ""):
        raise BiliError(
            "这是番剧的「媒体页」链接。请点进具体一集，用 /bangumi/play/ss… 或 ep… 的地址。"
        )

    data, bvid, _aid, final = _resolve_view(url, cookie, proxy, retries)
    title = (data.get("title") or "").strip()
    author = ((data.get("owner") or {}).get("name") or "").strip()
    pages = data.get("pages") or []

    if len(pages) > 1:
        source = "pages"
        items = []
        for position, page in enumerate(pages, start=1):
            number = int(page.get("page") or position)
            part = (page.get("part") or "").strip()
            items.append(
                {
                    "index": number,
                    "label": (f"P{number} {part}").strip(),
                    "duration": int(page.get("duration") or 0),
                    "url": f"https://www.bilibili.com/video/{bvid}?p={number}",
                    "key": f"bili:{bvid}:p{number}",
                }
            )
    else:
        items = _season_episodes(data.get("ugc_season") or {})
        source = "season" if len(items) > 1 else "single"

    return {
        "platform": "bilibili",
        "title": title,
        "author": author,
        "source": source,
        "has_multi": len(items) > 1,
        "current": _page_index(final),
        "episodes": items,
    }


def _extract_bangumi(kind: str, ident: int, cookie: str = "", proxy: str = "",
                     retries: int = 3, max_qn: int = 0,
                     allow_dash: bool = True) -> dict:
    """番剧 / 影视：走 pgc 接口取集数，再按权限拿播放地址。

    只下**当前账号本来就能播**的集：playurl 拿到什么就用什么，遇到
    大会员 / 地区限制直接报错，不做任何绕过。
    """
    season = _season(kind, ident, cookie, proxy, retries)
    items = _pgc_episodes(season)
    if not items:
        raise BiliError("这部番剧还没有可播放的正片（可能未开播，或需要大会员）。")

    chosen = items[0]
    if kind == "ep":
        chosen = next((ep for ep in items if ep["ep_id"] == ident), items[0])
    ep_id = chosen["ep_id"]
    raw_eps = list(season.get("episodes") or [])
    for section in season.get("sections") or []:
        raw_eps.extend(section.get("episodes") or [])
    raw = next((ep for ep in raw_eps if int(ep.get("id") or 0) == ep_id), {})
    cid = raw.get("cid") or (season.get("first_ep") or {}).get("cid")

    query = urllib.parse.urlencode(
        {
            "ep_id": ep_id,
            "cid": cid or "",
            "qn": int(max_qn or 120),
            "fnval": 4048,
            "fourk": 1,
        }
    )
    play = _get_json(f"{_PGC_PLAYURL}?{query}", cookie, proxy, retries)
    code = int(play.get("code") or 0)
    if code != 0:
        raise BiliError(
            _PGC_HINTS.get(code, f"番剧取流失败 code={code}：{play.get('message')}")
        )
    data = play.get("result") or play.get("data") or {}

    picked = None
    if allow_dash:
        picked = _assets_from_dash(data.get("dash") or {}, max_qn)
    if picked is None:
        durl = data.get("durl") or []
        quality = _quality_label(int(data.get("quality") or 0))
        if not durl:
            # 有些集只给整段：换 fnval=1 再问一次
            query = urllib.parse.urlencode(
                {"ep_id": ep_id, "cid": cid or "", "qn": 64, "fnval": 1, "fourk": 1}
            )
            fallback = _get_json(f"{_PGC_PLAYURL}?{query}", cookie, proxy, retries)
            fdata = fallback.get("result") or fallback.get("data") or {}
            durl = fdata.get("durl") or []
            quality = _quality_label(int(fdata.get("quality") or 64))
        urls = [item.get("url") for item in durl if item.get("url")]
        if not urls:
            raise BiliError(
                "这一集没有拿到播放地址：可能需要大会员、受地区限制，或该集尚未上线。"
            )
        asset = {
            "idx": 0,
            "kind": "video",
            "url": urls[0],
            "referer": "https://www.bilibili.com/",
        }
        if len(urls) > 1:
            asset["parts"] = urls
        picked = {
            "assets": [asset],
            "need_merge": False,
            "quality": quality,
            "quality_id": int(data.get("quality") or 0),
        }

    season_title = (season.get("title") or "").strip()
    episode_title = chosen["label"]
    title = f"{season_title} - {episode_title}" if season_title else episode_title

    meta = {
        "aweme_id": f"ep{ep_id}",
        "title": title,
        "author": _bangumi_author(season),
        "author_id": "",
        "kind": "video",
        "duration": chosen["duration"],
        "source_url": chosen["url"],
        "created": _as_int((season.get("publish") or {}).get("pub_time")),
        "platform": "bilibili",
    }
    return {**meta, **picked}


def extract(url: str, cookie: str = "", proxy: str = "", retries: int = 3,
            max_qn: int = 0, allow_dash: bool = True) -> dict:
    """解析一个 B 站稿件，返回可直接下载的任务描述。

    画质策略：优先走 DASH（`fnval=4048`，音视频分离但能拿到 1080P/4K/HDR），
    交给 `mp4mux` 无损合成单个 MP4；DASH 拿不到时退回 `fnval=1` 的整段 MP4。
    `max_qn` 用来限制最高档位（0 = 不限制）。
    """
    bangumi = parse_bangumi(url)
    if bangumi:
        return _extract_bangumi(bangumi[0], bangumi[1], cookie, proxy, retries,
                                max_qn, allow_dash)
    if _BANGUMI_MEDIA_RE.search(url or ""):
        raise BiliError(
            "这是番剧的「媒体页」链接。请点进具体一集，用 /bangumi/play/ss… 或 ep… 的地址。"
        )

    data, bvid, av_id, final = _resolve_view(url, cookie, proxy, retries)
    pages = data.get("pages") or []
    index = _page_index(final)
    if pages and index > len(pages):
        raise BiliError(f"该视频只有 {len(pages)} 个分 P，你请求的是 P{index}。")
    cid = pages[index - 1]["cid"] if pages else data["cid"]

    title = (data.get("title") or "").strip() or data.get("bvid", "bilibili")
    if len(pages) > 1:
        part = (pages[index - 1].get("part") or "").strip()
        if part:
            title = f"{title} - P{index} {part}"

    author = ((data.get("owner") or {}).get("name") or "unknown").strip()
    duration = int(data.get("duration") or 0)

    meta = {
        "aweme_id": bvid or f"av{data.get('aid')}",
        "title": title,
        "author": author,
        "author_id": str((data.get("owner") or {}).get("mid") or ""),
        "kind": "video",
        "duration": duration,
        "source_url": f"https://www.bilibili.com/video/{bvid}",
        "created": int(data.get("pubdate") or data.get("ctime") or 0),
        "platform": "bilibili",
    }

    # DASH 与整段 MP4 的档位不一定一致：匿名时 DASH 往往只给 480P，而整段
    # MP4 能到 720P。所以两条路都问一遍，按**实际能拿到的档位**选，档位相同
    # 时优先整段 MP4（单个文件、不用合成）。
    dash_meta = None
    if allow_dash:
        try:
            dash_meta = _dash_assets(bvid, cid, cookie, proxy, retries, max_qn)
        except BiliError:
            dash_meta = None

    progressive = None
    if dash_meta is None or dash_meta["quality_id"] < 80:
        try:
            durl, durl_qn = _pick_durl(bvid, cid, cookie, proxy, retries)
        except BiliError:
            if dash_meta is None:
                raise
            durl, durl_qn = [], 0
        if durl:
            progressive = _progressive_assets(durl, durl_qn)

    if progressive and (
        dash_meta is None or progressive["quality_id"] >= dash_meta["quality_id"]
    ):
        return {**meta, **progressive}
    if dash_meta is not None:
        return {**meta, **dash_meta}
    raise BiliError("接口没有返回可下载地址（可能被限制或需要登录）。")


def _progressive_assets(durl: list[dict], qn: int) -> dict:
    """整段 MP4（`fnval=1`）：一个文件，老视频可能是多段 durl。"""
    play_urls = [item.get("url") for item in durl if item.get("url")]
    asset = {
        "idx": 0,
        "kind": "video",
        "url": play_urls[0],
        "referer": "https://www.bilibili.com/",
    }
    if len(play_urls) > 1:
        # 老视频会拆成多段，按顺序拼接，不做转码
        asset["parts"] = play_urls
    return {
        "assets": [asset],
        "need_merge": False,
        "quality": _quality_label(qn),
        "quality_id": int(qn or 0),
    }


# DASH 里同一个清晰度会同时给 avc1 / hevc / av01，优先 avc1：兼容性最好，
# 播放器不装解码器也能放
_CODEC_RANK = {7: 0, 12: 1, 13: 2}

_QUALITY_LABEL = {
    127: "8K", 126: "杜比视界", 125: "HDR", 120: "4K", 116: "1080P60",
    112: "1080P+", 80: "1080P", 74: "720P60", 64: "720P", 32: "480P", 16: "360P",
}

# 纯 AAC 音轨；30250 是杜比（ec-3）、30251 是 Hi-Res（flac），部分播放器不认
_AAC_AUDIO_IDS = (30216, 30232, 30280)


def _quality_label(qn: int) -> str:
    return _QUALITY_LABEL.get(int(qn or 0), f"qn{qn}")


def _backup_urls(stream: dict) -> list[str]:
    urls = [stream.get("baseUrl") or stream.get("base_url") or ""]
    for extra in (stream.get("backupUrl") or stream.get("backup_url") or []):
        if extra:
            urls.append(extra)
    return [url for url in urls if url]


def _pick_video(streams: list[dict], max_qn: int = 0) -> dict | None:
    """按「清晰度优先、同清晰度优先 avc1」挑一路视频。"""
    usable = [s for s in streams if _backup_urls(s)]
    if not usable:
        return None
    cap = int(max_qn or 0)
    if cap > 0:
        capped = [s for s in usable if int(s.get("id") or 0) <= cap]
        if capped:
            usable = capped
        else:
            # 用户设的上限比平台最低档还低：给最低档，别反而下最大的
            return min(
                usable,
                key=lambda s: (int(s.get("id") or 0),
                               _CODEC_RANK.get(int(s.get("codecid") or 0), 9)),
            )
    return max(
        usable,
        key=lambda s: (int(s.get("id") or 0),
                       -_CODEC_RANK.get(int(s.get("codecid") or 0), 9)),
    )


def _pick_audio(streams: list[dict]) -> dict | None:
    """优先纯 AAC 里码率最高的一路，避免杜比 / Hi-Res 在普通播放器里没声。"""
    usable = [s for s in streams if _backup_urls(s)]
    if not usable:
        return None
    aac = [s for s in usable if int(s.get("id") or 0) in _AAC_AUDIO_IDS]
    pool = aac or usable
    return max(pool, key=lambda s: int(s.get("bandwidth") or 0))


def _assets_from_dash(dash: dict, max_qn: int = 0) -> dict | None:
    """从任意 playurl 响应的 `dash` 里挑出两路资产（普通稿件和番剧共用）。"""
    video = _pick_video(dash.get("video") or [], max_qn)
    if not video:
        return None
    audio = _pick_audio(dash.get("audio") or [])

    assets = [
        {
            "idx": 0,
            "kind": "video",
            "url": _backup_urls(video)[0],
            "urls": _backup_urls(video),
            "referer": "https://www.bilibili.com/",
            "role": "video",
        }
    ]
    if audio:
        assets.append(
            {
                "idx": 1,
                "kind": "audio",
                "url": _backup_urls(audio)[0],
                "urls": _backup_urls(audio),
                "referer": "https://www.bilibili.com/",
                "role": "audio",
            }
        )

    qn = int(video.get("id") or 0)
    quality = _quality_label(qn)
    height = video.get("height")
    if height:
        quality = f"{quality} {height}P" if str(height) not in quality else quality
    return {
        "assets": assets,
        "need_merge": True,
        "quality": quality,
        "quality_id": qn,
        "has_dash_audio": bool(audio),
    }


def _dash_assets(bvid: str, cid: int, cookie: str, proxy: str, retries: int,
                 max_qn: int = 0) -> dict | None:
    """请求 DASH 播放地址，返回两条资产（视频 + 音频）。"""
    query = urllib.parse.urlencode(
        {
            "bvid": bvid,
            "cid": cid,
            "qn": int(max_qn or 120),
            "fnval": 4048,     # DASH + HDR + 4K + 杜比音频 + 杜比视界 + 8K + AV1
            "fourk": 1,
        }
    )
    payload = _get_json(f"{_PLAYURL}?{query}", cookie, proxy, retries)
    if int(payload.get("code") or 0) != 0:
        return None
    data = payload.get("data") or {}
    return _assets_from_dash(data.get("dash") or {}, max_qn)


def _pick_durl(bvid: str, cid: int, cookie: str, proxy: str,
               retries: int) -> tuple[list[dict], int]:
    """整段 MP4 的下载地址，返回 (durl 列表, 实际档位 qn)。"""
    last_message = ""
    for qn in _QN_ORDER:
        query = urllib.parse.urlencode(
            {"bvid": bvid, "cid": cid, "qn": qn, "fnval": 1, "fourk": 0}
        )
        payload = _get_json(f"{_PLAYURL}?{query}", cookie, proxy, retries)
        if int(payload.get("code") or 0) != 0:
            last_message = payload.get("message") or f"code={payload.get('code')}"
            continue
        data = payload.get("data") or {}
        durl = data.get("durl") or []
        if durl:
            # 接口会按账号权限自动降档，真实档位在 data.quality 里
            return durl, int(data.get("quality") or qn)
        last_message = "durl 为空"
    raise BiliError(f"拿不到播放地址：{last_message}")


# --------------------------------------------------------------------------
# UP 主作品列表（WBI 签名）
# --------------------------------------------------------------------------


class _Session:
    """带 WBI 签名的会话：匿名 Cookie（buvid3）+ 可选登录 Cookie。"""

    def __init__(self, cookie: str = "", proxy: str = "") -> None:
        self.jar = http.cookiejar.CookieJar()
        handlers: list = [urllib.request.HTTPCookieProcessor(self.jar)]
        if proxy:
            handlers.append(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            )
        self.opener = urllib.request.build_opener(*handlers)
        self._mixin_key: str | None = None
        self._mixin_at = 0.0
        for item in (cookie or "").split(";"):
            name, _, value = item.strip().partition("=")
            if name and value:
                self.jar.set_cookie(
                    http.cookiejar.Cookie(
                        version=0, name=name.strip(), value=value.strip(),
                        port=None, port_specified=False,
                        domain=".bilibili.com", domain_specified=True,
                        domain_initial_dot=True, path="/", path_specified=True,
                        secure=False, expires=None, discard=True,
                        comment=None, comment_url=None, rest={}, rfc2109=False,
                    )
                )

    def _headers(self, referer: str = "https://www.bilibili.com/") -> dict:
        return {
            "User-Agent": UA,
            "Referer": referer,
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }

    def get_json(self, url: str, referer: str = "https://www.bilibili.com/") -> dict:
        req = urllib.request.Request(url, headers=self._headers(referer))
        try:
            with self.opener.open(req, timeout=25) as resp:
                return json.loads(decode_text(resp.read(4_000_000)))
        except BiliError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BiliError(f"请求 B 站失败：{exc}") from exc

    def bootstrap(self) -> None:
        """拿稳定的匿名 Cookie：buvid3/buvid4。缺了很容易 412 或 -352。"""
        try:
            self.opener.open(
                urllib.request.Request(
                    "https://www.bilibili.com/",
                    headers={"User-Agent": UA, "Accept": "text/html"},
                ),
                timeout=15,
            ).read(1024)
        except Exception:  # noqa: BLE001
            pass
        try:
            data = self.get_json("https://api.bilibili.com/x/frontend/finger/spi")
            payload = data.get("data") or {}
            for name, key in (("buvid3", "b_3"), ("buvid4", "b_4")):
                value = payload.get(key)
                if value:
                    self._set_cookie(name, value)
        except Exception:  # noqa: BLE001
            pass

    def _set_cookie(self, name: str, value: str) -> None:
        self.jar.set_cookie(
            http.cookiejar.Cookie(
                version=0, name=name, value=value, port=None, port_specified=False,
                domain=".bilibili.com", domain_specified=True,
                domain_initial_dot=True, path="/", path_specified=True,
                secure=False, expires=None, discard=False,
                comment=None, comment_url=None, rest={}, rfc2109=False,
            )
        )

    def mixin_key(self) -> str:
        if self._mixin_key and time.time() - self._mixin_at < 3600:
            return self._mixin_key
        nav = self.get_json(_NAV)
        wbi = (nav.get("data") or {}).get("wbi_img") or {}
        img = (wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
        sub = (wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
        if not img or not sub:
            raise BiliError("拿不到 WBI key，接口可能改版了")
        raw = img + sub
        self._mixin_key = "".join(raw[i] for i in _MIXIN if i < len(raw))[:32]
        self._mixin_at = time.time()
        return self._mixin_key

    def signed_query(self, params: dict) -> str:
        items = {k: str(v) for k, v in params.items()}
        items["wts"] = str(int(time.time()))
        clean = sorted(
            (k, "".join(ch for ch in v if ch not in "!'()*"))
            for k, v in items.items()
        )
        query = urllib.parse.urlencode(clean)
        w_rid = hashlib.md5((query + self.mixin_key()).encode()).hexdigest()
        return f"{query}&w_rid={w_rid}"


def _length_to_seconds(text: str) -> int:
    total = 0
    for part in str(text or "").split(":"):
        if part.isdigit():
            total = total * 60 + int(part)
    return total


def _cover(url: str | None) -> str | None:
    if not url:
        return None
    return f"https:{url}" if url.startswith("//") else url


_SESSION_CACHE: dict[str, "_Session"] = {}
_SESSION_LOCK = threading.Lock()


def _get_session(cookie: str = "", proxy: str = "") -> "_Session":
    """复用会话：buvid3 每次变会让 B 站更容易判成爬虫（-352）。"""
    key = f"{cookie}|{proxy}"
    with _SESSION_LOCK:
        session = _SESSION_CACHE.get(key)
        if session is None:
            session = _Session(cookie, proxy)
            session.bootstrap()
            _SESSION_CACHE[key] = session
        return session


def _reset_session(cookie: str = "", proxy: str = "") -> None:
    """412/403 时换个新会话（重新领匿名 Cookie）。"""
    with _SESSION_LOCK:
        _SESSION_CACHE.pop(f"{cookie}|{proxy}", None)


def user_videos(mid: str, page: int = 1, cookie: str = "", proxy: str = "",
                ps: int = 30) -> dict:
    """UP 主投稿列表，返回结构与抖音主页面板一致，可直接复用前端。"""
    session = _get_session(cookie, proxy)
    params = {
        "mid": mid,
        "ps": ps,
        "pn": max(1, page),
        "order": "pubdate",
        "platform": "web",
        "web_location": 1550101,
        "dm_img_list": "[]",
        "dm_img_str": "V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ",
        "dm_cover_img_str": (
            "QU5HTEUgKE5WSURJQSwgTlZJRElBIEdlRm9yY2UgR1RYIDE2NTAgKDB4MDAwMDFGOTEp"
            "IERpcmVjdDNEMTEgdnNfNV8wIHBzXzVfMCwgRDNEMTEpR29vZ2xlIEluYy4gKE5WSURJQS"
        ),
        "dm_img_inter": '{"ds":[],"wh":[0,0,0],"of":[0,0,0]}',
    }
    data: dict = {}
    last_error = ""
    for attempt in range(3):
        try:
            data = session.get_json(f"{_SPACE_ARC}?{session.signed_query(params)}")
        except BiliError as exc:
            last_error = str(exc)
            if "412" in last_error or "403" in last_error:
                # B 站前置校验失败：换新会话再试
                _reset_session(cookie, proxy)
                session = _get_session(cookie, proxy)
                time.sleep(2.0 + attempt * 2.0)
                continue
            raise

        code = int(data.get("code") or 0)
        if code == -352:
            last_error = "风控 -352"
            # -352 基本是这一份匿名会话（buvid3）被标记了：换新会话重新领
            # 匿名 Cookie，光 sleep + 复用旧会话没用。
            _reset_session(cookie, proxy)
            time.sleep(1.5 + attempt * 2.0)
            session = _get_session(cookie, proxy)
            continue
        if code != 0:
            raise BiliError(
                _CODE_HINTS.get(code, f"B 站返回 code={code}：{data.get('message')}")
            )
        break
    else:
        raise BiliError(
            f"B 站拦截，重试 3 次仍失败（{last_error or '未知原因'}）。"
            "等几秒再试，或在设置里填 B 站 Cookie。"
        )

    payload = data.get("data") or {}
    vlist = ((payload.get("list") or {}).get("vlist")) or []
    page_info = payload.get("page") or {}
    total = int(page_info.get("count") or 0)
    nickname = ((payload.get("card") or {}).get("name") or "").strip()

    items = []
    for it in vlist:
        items.append(
            {
                "aweme_id": it.get("bvid") or "",
                "desc": (it.get("title") or "").strip(),
                "cover": _cover(it.get("pic")),
                "kind": "video",
                "author": (it.get("author") or nickname).strip(),
                "sec_uid": str(mid),
                "author_id": str(mid),
                "play_url": None,  # 下载时按 bvid 现解析，避免直链过期
                "images": [],
                "digg": 0,
                "comment": 0,
                "duration": _length_to_seconds(it.get("length")),
                "created": int(it.get("created") or 0),
                "platform": "bilibili",
            }
        )

    return {
        "items": items,
        "total": total,
        "page": max(1, page),
        "has_more": max(1, page) * ps < total,
        # arc/search 的 card 有时不含昵称，退回作品作者名
        "nickname": nickname or (items[0]["author"] if items else ""),
    }


# --------------------------------------------------------------------------
# 账号登录（扫码）
# --------------------------------------------------------------------------

_LOGIN_LOCK = threading.Lock()
_LOGIN_STATE: dict = {"status": "idle", "message": "", "account": None}
_LOGIN_CANCEL = threading.Event()
# 登录后需要留住的 Cookie
_KEEP_COOKIES = (
    "SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5",
    "sid", "buvid3", "buvid4",
)


def login_state() -> dict:
    with _LOGIN_LOCK:
        return dict(_LOGIN_STATE)


def _set_login_state(status: str, message: str, account: dict | None = None) -> None:
    with _LOGIN_LOCK:
        _LOGIN_STATE.update({"status": status, "message": message, "account": account})


def start_login(timeout: int = 240) -> dict:
    """开一个**有界面**的浏览器让用户扫码，扫完把 Cookie 收进来。

    用有头浏览器而不是自己画二维码：登录页的二维码、风控、二次验证都交给 B 站自己处理，
    我们只负责在登录成功后把 Cookie 取出来。
    """
    with _LOGIN_LOCK:
        if _LOGIN_STATE["status"] == "waiting":
            return dict(_LOGIN_STATE)
        _LOGIN_CANCEL.clear()
        _LOGIN_STATE.update(
            {
                "status": "waiting",
                "message": "请在弹出的浏览器窗口里用 B 站 App 扫码",
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
        browser.open_page(_LOGIN_PAGE)
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
                if "bilibili" in (c.get("domain") or "")
            }
            if jar.get("SESSDATA"):
                break

        if not jar.get("SESSDATA"):
            _set_login_state("timeout", "等待超时：没有检测到登录")
            return

        cookie = "; ".join(f"{k}={jar[k]}" for k in _KEEP_COOKIES if jar.get(k))
        account = _account_info(cookie)
        from . import config as config_mod

        config_mod.save({"bilibili_cookie": cookie})
        _set_login_state(
            "ok",
            f"已登录：{account.get('uname') or '未知账号'}",
            account,
        )
    except Exception as exc:  # noqa: BLE001
        _set_login_state("error", f"登录失败：{exc}")
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass


def _account_info(cookie: str) -> dict:
    session = _Session(cookie)
    try:
        nav = session.get_json(_NAV)
    except BiliError:
        return {"uname": "", "mid": 0, "is_login": False}
    data = nav.get("data") or {}
    return {
        "uname": data.get("uname") or "",
        "mid": data.get("mid") or 0,
        "is_login": bool(data.get("isLogin")),
        "vip": bool(data.get("vipStatus") or 0),
    }


def logout() -> None:
    from . import config as config_mod

    _LOGIN_CANCEL.set()
    config_mod.save({"bilibili_cookie": ""})
    _set_login_state("idle", "已退出登录")
