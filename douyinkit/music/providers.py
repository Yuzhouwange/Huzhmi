"""音乐平台适配器：网易云 / QQ 音乐 / 酷狗 / 酷我。

只做四件事：**搜歌、读专辑/歌单、拿歌词评论、解析可下载直链**。
每个平台都可能因为版权、会员、风控而不给直链——这时抛 `ProviderError`
（带人话原因），而不是返回一个 0 字节的假链接。

所有方法返回纯 JSON 友好的 dict，前端/服务层不用认识平台差异：

    {"provider","id","title","artist","album","duration","cover","ext","quality"}

平台接口是公开网页接口，不需要 App 签名；失败时逐级降级，最多重试两次。
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import random
import re
import time
import ast
import urllib.error
import urllib.parse
import urllib.request

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 15
_RETRY = 2


class ProviderError(RuntimeError):
    """平台没给数据/直链（版权、会员、风控、接口变更）。"""


def _request(url: str, headers: dict | None = None, data: bytes | None = None,
             timeout: float = DEFAULT_TIMEOUT) -> bytes:
    last: Exception | None = None
    merged = {"User-Agent": UA, "Accept": "*/*", **(headers or {})}
    for attempt in range(_RETRY + 1):
        try:
            req = urllib.request.Request(url, headers=merged, data=data)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (403, 404, 429):
                break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
        if attempt < _RETRY:
            time.sleep(0.4 * (attempt + 1))
    raise ProviderError(f"请求失败：{last}")


def _json(url: str, headers: dict | None = None, data: bytes | None = None,
          timeout: float = DEFAULT_TIMEOUT) -> dict:
    raw = _request(url, headers, data, timeout)
    try:
        payload = json.loads(raw.decode("utf-8", "replace").lstrip("\ufeff"))
    except ValueError as exc:
        raise ProviderError(f"平台返回的不是 JSON：{raw[:80]!r}") from exc
    if not isinstance(payload, dict):
        raise ProviderError("平台返回格式异常")
    return payload


def _text(url: str, headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> str:
    return _request(url, headers, timeout=timeout).decode("utf-8", "replace")


def _get(url: str, headers: dict | None = None) -> dict:
    """所有平台的 GET 包装：短超时 + 统一错误。"""
    return _json(url, headers)


def _text_value(value: object) -> str:
    return html.unescape(str(value or "")).replace("\xa0", " ").strip()


def song(provider: str, sid: str, title: str, artist: str, *,
         album: str = "", album_id: str = "", duration: float = 0,
         cover: str = "", ext: str = "mp3", quality: str = "",
         mid: str = "", available: bool = True, reason: str = "",
         extra: dict | None = None) -> dict:
    return {
        "provider": provider,
        "id": str(sid or ""),
        "mid": str(mid or ""),
        "title": _text_value(title) or "未命名",
        "artist": _text_value(artist) or "未知歌手",
        "album": _text_value(album),
        "album_id": str(album_id or ""),
        "duration": round(float(duration or 0), 2),
        "cover": str(cover or ""),
        "ext": (ext or "mp3").lower(),
        "quality": quality or "",
        "available": bool(available),
        "reason": reason,
        **(extra or {}),
    }


def _ids(values: list[str], size: int = 100) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


class Provider:
    name = ""
    label = ""
    supports_album = True
    supports_playlist = True
    supports_rank = True
    supports_comments = True
    referer = ""

    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or {}
        self.cookie = str(self.cfg.get(f"music_cookie_{self.name}") or "").strip()

    # -- 子类实现 --
    def search(self, keyword: str, page: int = 1, limit: int = 30) -> list[dict]:
        raise NotImplementedError

    def album(self, album_id: str) -> dict:
        raise ProviderError(f"{self.label} 暂不支持读取专辑")

    def playlist(self, playlist_id: str) -> dict:
        raise ProviderError(f"{self.label} 暂不支持读取歌单")

    def rank(self, rank_id: str) -> dict:
        raise ProviderError(f"{self.label} 暂不支持排行榜")

    def lyrics(self, item: dict) -> dict:
        raise ProviderError(f"{self.label} 暂不支持歌词")

    def comments(self, item: dict, page: int = 1, limit: int = 20) -> dict:
        raise ProviderError(f"{self.label} 暂不支持评论")

    def cover(self, item: dict) -> str:
        """封面 URL；搜索结果没带就由平台补一次。"""
        return str(item.get("cover") or "")

    def resolve(self, item: dict) -> dict:
        raise ProviderError(f"{self.label} 暂不支持下载")

    # -- 工具 --
    @staticmethod
    def _cookie_value(cookie: str, *names: str) -> str:
        for part in str(cookie or "").split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            if key.strip() in names:
                return value.strip()
        return ""

    def _headers(self, **extra) -> dict:
        headers = {"Referer": self.referer} if self.referer else {}
        if self.cookie:
            headers["Cookie"] = self.cookie
        headers.update(extra)
        return headers

    @staticmethod
    def collection(title: str, cover: str, artist: str, items: list[dict],
                   **extra) -> dict:
        return {
            "title": title or "未命名",
            "cover": cover or "",
            "artist": artist or "",
            "count": len(items),
            "items": items,
            **extra,
        }


# ---------------------------------------------------------------------------
# 网易云
# ---------------------------------------------------------------------------

_NETEASE_API = "https://music.163.com"


class NeteaseProvider(Provider):
    name = "netease"
    label = "网易云"
    referer = "https://music.163.com/"

    def _headers(self, **extra) -> dict:
        headers = super()._headers(**extra)
        cookie = headers.get("Cookie") or ""
        if cookie and "os=" not in cookie:
            # 带 os=pc 才会拿到桌面端的音质档位
            headers["Cookie"] = cookie + "; os=pc"
        return headers

    def _get(self, path: str) -> dict:
        """网易云会随机回 `code=-462`（风控），重试几次比直接报错有用。"""
        last: dict = {}
        for attempt in range(4):
            data = _json(f"{_NETEASE_API}{path}", self._headers())
            if data.get("code") != -462:
                return data
            last = data
            time.sleep(0.6 * (attempt + 1))
        raise ProviderError("网易云风控拦截（-462），稍后再试，或在设置里填网易云 Cookie")

    def _song(self, raw: dict) -> dict:
        artists = raw.get("ar") or raw.get("artists") or []
        if isinstance(artists, dict):
            artists = [artists]
        album_obj = raw.get("al") or raw.get("album") or {}
        if isinstance(album_obj, str):
            # /api/song/detail 批量返回时 album 直接是专辑名
            album_name, album_id, cover = album_obj, "", ""
        else:
            album_name = album_obj.get("name") or ""
            album_id = album_obj.get("id") or ""
            cover = album_obj.get("picUrl") or ""
        names = []
        for artist in artists if isinstance(artists, list) else []:
            if isinstance(artist, dict):
                names.append(str(artist.get("name") or ""))
            elif isinstance(artist, str):
                names.append(artist)
        fee = raw.get("fee")
        available = fee not in (4,)          # 4 = 无版权
        return song(
            self.name,
            raw.get("id"),
            raw.get("name") or raw.get("mainTitle") or "",
            " / ".join([name for name in names if name]) or "未知歌手",
            album=album_name,
            album_id=album_id,
            duration=float(raw.get("dt") or raw.get("duration") or 0) / 1000.0,
            cover=cover,
            available=available,
            reason="" if available else "该曲在网易云无版权",
        )

    def cover(self, item: dict) -> str:
        url = super().cover(item)
        if url:
            return url
        sid = urllib.parse.quote(str(item.get("id") or ""))
        try:
            data = self._get(f"/api/song/detail?ids=%5B{sid}%5D")
        except ProviderError:
            return ""
        songs = data.get("songs") or []
        if not songs:
            return ""
        raw = songs[0]
        album = raw.get("al") or raw.get("album") or {}
        return str(album.get("picUrl") or "") if isinstance(album, dict) else ""

    def search(self, keyword: str, page: int = 1, limit: int = 30) -> list[dict]:
        limit = max(1, min(100, int(limit or 30)))
        offset = (max(1, int(page or 1)) - 1) * limit
        url = (f"/api/search/get/web?s={urllib.parse.quote(keyword)}"
               f"&type=1&limit={limit}&offset={offset}")
        data = self._get(url)
        result = data.get("result") or {}
        return [self._song(item) for item in result.get("songs") or []]

    def _detail(self, ids: list[str]) -> list[dict]:
        """批量取歌曲详情，返回**原始**结构（交给 `_song` 统一归一化）。"""
        items: list[dict] = []
        for chunk in _ids([str(i) for i in ids], 100):
            url = ("/api/song/detail?ids="
                   + urllib.parse.quote(json.dumps(chunk)))
            data = self._get(url)
            items.extend(raw for raw in data.get("songs") or []
                         if isinstance(raw, dict))
        return items

    def album(self, album_id: str) -> dict:
        raw_id = str(album_id)
        quoted = urllib.parse.quote(raw_id)
        data = self._get(f"/api/v1/album/{quoted}")
        album = data.get("album") or {}
        songs = data.get("songs") or album.get("songs") or []
        if not songs:
            # 老接口兜底（v1 偶尔只给元数据）
            fallback = self._get(f"/api/album/{quoted}")
            album = fallback.get("album") or album
            songs = album.get("songs") or []
        if not album and not songs:
            raise ProviderError("网易云没有这个专辑")
        artists = album.get("artist") or {}
        items = [self._song(raw) for raw in songs]
        return self.collection(album.get("name") or "", album.get("picUrl") or "",
                               artists.get("name") or "", items, kind="album",
                               id=raw_id, provider=self.name)

    def playlist(self, playlist_id: str) -> dict:
        data = self._get(f"/api/v6/playlist/detail?id={urllib.parse.quote(str(playlist_id))}")
        playlist = data.get("playlist") or {}
        if not playlist:
            raise ProviderError("网易云没有这个歌单")
        tracks = playlist.get("tracks") or []
        total = int(playlist.get("trackCount") or 0)
        if len(tracks) < total:
            track_ids = [str(t.get("id")) for t in playlist.get("trackIds") or []
                         if t.get("id")]
            if track_ids:
                tracks = self._detail(track_ids)
        items = [self._song(raw) for raw in tracks]
        creator = (playlist.get("creator") or {}).get("nickname") or ""
        return self.collection(playlist.get("name") or "",
                               playlist.get("coverImgUrl") or "", creator, items,
                               kind="playlist", id=str(playlist_id), provider=self.name)

    def rank(self, rank_id: str) -> dict:
        data = self.playlist(str(rank_id))
        data["kind"] = "rank"
        return data

    def lyrics(self, item: dict) -> dict:
        url = (f"/api/song/lyric?id={urllib.parse.quote(str(item.get('id')))}"
               "&lv=1&kv=1&tv=-1")
        data = self._get(url)
        lrc = (data.get("lrc") or {}).get("lyric") or ""
        translated = (data.get("tlyric") or {}).get("lyric") or ""
        if not lrc.strip():
            raise ProviderError("这首歌没有歌词")
        return {"lrc": lrc, "translated": translated}

    def comments(self, item: dict, page: int = 1, limit: int = 20) -> dict:
        limit = max(1, min(100, int(limit or 20)))
        offset = (max(1, int(page or 1)) - 1) * limit
        url = (f"/api/v1/resource/comments/R_SO_4_{urllib.parse.quote(str(item.get('id')))}"
               f"?limit={limit}&offset={offset}")
        data = self._get(url)
        rows = []
        for raw in (data.get("hotComments") or []) + (data.get("comments") or []):
            user = raw.get("user") or {}
            rows.append({
                "user": user.get("nickname") or "匿名",
                "avatar": user.get("avatarUrl") or "",
                "content": raw.get("content") or "",
                "likes": raw.get("likedCount") or 0,
                "time": raw.get("time") or 0,
            })
        return {"items": rows, "total": int(data.get("total") or 0),
                "provider": self.name}

    def resolve(self, item: dict) -> dict:
        sid = urllib.parse.quote(str(item.get("id") or ""))
        headers = self._headers()
        # 登录后先要无损，再退 320k / 192k / 128k；没登录时直接 320k 起步
        levels = (("lossless", "flac"), ("exhigh", "mp3"), ("higher", "mp3"),
                  ("standard", "mp3")) if self.cookie else \
                 (("exhigh", "mp3"), ("standard", "mp3"))
        for level, encode in levels:
            try:
                data = self._get(
                    f"/api/song/enhance/player/url/v1?id={sid}"
                    f"&ids=%5B{sid}%5D&level={level}&encodeType={encode}")
            except ProviderError:
                continue
            row = (data.get("data") or [{}])[0]
            if row.get("url"):
                ext = str(row.get("type") or encode or "mp3").lower()
                bitrate = int(row.get("br") or 0)
                if ext == "flac":
                    quality = "无损"
                elif bitrate >= 320000:
                    quality = "320k"
                elif bitrate >= 192000:
                    quality = "192k"
                else:
                    quality = "128k"
                return {"url": row["url"], "ext": ext,
                        "quality": quality, "headers": headers,
                        "provider": self.name, "level": level}
        outer = f"{_NETEASE_API}/song/media/outer/url?id={sid}.mp3"
        # 注意：不要用 Range 探活——带 Range 时网易云常常回一坨 83 字节的
        # JSON 错误而不是音频。直接读前 1 KB，看完就关连接。
        try:
            probe = urllib.request.Request(outer, headers=headers)
            with urllib.request.urlopen(probe, timeout=DEFAULT_TIMEOUT) as resp:
                ctype = (resp.headers.get("Content-Type") or "").lower()
                size = int(resp.headers.get("Content-Length") or 0)
                head = resp.read(1024)
                final = resp.geturl()
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            raise ProviderError(f"网易云直链请求失败：{exc}") from exc
        if "html" in ctype or "json" in ctype or size < 65536 or head[:1] in (b"{", b"<"):
            raise ProviderError("网易云没有给直链（该曲可能需要会员或无版权）")
        quality = "320k" if ("m801" in final or "320" in final) else "128k"
        return {"url": outer, "ext": "mp3", "quality": quality,
                "headers": headers, "provider": self.name}


# ---------------------------------------------------------------------------
# QQ 音乐
# ---------------------------------------------------------------------------

_QQ_API = "https://c.y.qq.com"


class QQProvider(Provider):
    name = "qq"
    label = "QQ音乐"
    referer = "https://y.qq.com/"

    def _song(self, raw: dict) -> dict:
        singers = raw.get("singer") or []
        names = [str(s.get("name") or "") for s in singers if isinstance(s, dict)]
        mid = raw.get("songmid") or raw.get("mid") or ""
        album_mid = raw.get("albummid") or ""
        album_obj = raw.get("album")
        album_name = raw.get("albumname") or (
            album_obj.get("name") if isinstance(album_obj, dict) else "") or ""
        return song(
            self.name,
            raw.get("songid") or raw.get("songId") or mid,
            raw.get("songname") or raw.get("name") or raw.get("title") or "",
            " / ".join([n for n in names if n]) or "未知歌手",
            album=album_name,
            album_id=raw.get("albumid") or raw.get("albummid") or "",
            duration=float(raw.get("interval") or 0),
            cover=(f"https://y.gtimg.cn/music/photo_new/T002R300x300M000{album_mid}.jpg"
                   if album_mid else ""),
            mid=str(mid),
        )

    def search(self, keyword: str, page: int = 1, limit: int = 30) -> list[dict]:
        limit = max(1, min(50, int(limit or 30)))
        page = max(1, int(page or 1))
        url = (f"{_QQ_API}/soso/fcgi-bin/client_search_cp?p={page}&n={limit}"
               f"&w={urllib.parse.quote(keyword)}&format=json")
        data = _json(url, self._headers())
        songs = ((data.get("data") or {}).get("song") or {}).get("list") or []
        return [self._song(raw) for raw in songs]

    def album(self, album_id: str) -> dict:
        url = (f"{_QQ_API}/v8/fcg-bin/fcg_v8_album_info_cp.fcg?albummid="
               f"{urllib.parse.quote(str(album_id))}&format=json&g_tk=5381")
        data = _json(url, self._headers())
        info = data.get("data") or {}
        if not info:
            raise ProviderError("QQ 音乐没有这个专辑")
        items = [self._song(raw) for raw in info.get("list") or []]
        cover = (f"https://y.gtimg.cn/music/photo_new/T002R300x300M000{album_id}.jpg")
        return self.collection(info.get("name") or "", cover,
                               info.get("singername") or "", items, kind="album",
                               id=str(album_id), provider=self.name)

    def playlist(self, playlist_id: str) -> dict:
        url = (f"{_QQ_API}/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg?type=1&json=1"
               f"&utf8=1&onlysong=0&disstid={urllib.parse.quote(str(playlist_id))}"
               "&format=json&g_tk=5381&loginUin=0&hostUin=0&inCharset=utf8"
               "&outCharset=utf-8&notice=0&platform=yqq.json&needNewCode=0")
        data = _json(url, self._headers())
        cdlist = data.get("cdlist") or []
        if not cdlist:
            raise ProviderError("QQ 音乐没有这个歌单")
        cd = cdlist[0]
        items = [self._song(raw) for raw in cd.get("songlist") or []]
        return self.collection(cd.get("dissname") or "", cd.get("logo") or "",
                               cd.get("nickname") or cd.get("login") or "", items,
                               kind="playlist", id=str(playlist_id), provider=self.name)

    def rank(self, rank_id: str) -> dict:
        url = (f"{_QQ_API}/v8/fcg-bin/fcg_v8_toplist_cp.fcg?topid={urllib.parse.quote(str(rank_id))}"
               "&format=json&page=detail&type=top&song_begin=0&song_num=100&g_tk=5381")
        data = _json(url, self._headers())
        rows = data.get("songlist") or []
        items = []
        for row in rows:
            raw = row.get("data") if isinstance(row.get("data"), dict) else row
            if isinstance(raw, dict):
                items.append(self._song(raw))
        if not items:
            raise ProviderError("QQ 音乐排行榜没有数据")
        top = data.get("topinfo") or {}
        return self.collection(top.get("ListName") or f"排行榜 {rank_id}",
                               top.get("pic") or "", "", items, kind="rank",
                               id=str(rank_id), provider=self.name)

    def lyrics(self, item: dict) -> dict:
        mid = item.get("mid") or item.get("id")
        url = (f"{_QQ_API}/lyric/fcgi-bin/fcg_query_lyric_new.fcg?songmid="
               f"{urllib.parse.quote(str(mid))}&format=json&nobase64=1&g_tk=5381")
        data = _json(url, self._headers())
        lrc = html.unescape(str(data.get("lyric") or ""))
        translated = html.unescape(str(data.get("trans") or ""))
        if not lrc.strip():
            raise ProviderError("这首歌没有歌词")
        return {"lrc": lrc, "translated": translated}

    def comments(self, item: dict, page: int = 1, limit: int = 20) -> dict:
        limit = max(1, min(50, int(limit or 20)))
        page = max(0, int(page or 1) - 1)
        url = (f"{_QQ_API}/base/fcgi-bin/fcg_global_comment_h5.fcg?g_tk=5381"
               "&loginUin=0&hostUin=0&format=json&inCharset=utf8&outCharset=utf-8"
               "&notice=0&platform=yqq.json&needNewCode=0&cid=205360772&reqtype=2"
               f"&biztype=1&topid={urllib.parse.quote(str(item.get('id') or ''))}"
               f"&cmd=8&needmusiccrit=0&pagenum={page}&pagesize={limit}")
        data = _json(url, self._headers())
        rows = []
        for key in ("hot_comment", "comment"):
            block = data.get(key) or {}
            for raw in block.get("commentlist") or []:
                rows.append({
                    "user": raw.get("nick") or raw.get("uin") or "匿名",
                    "avatar": raw.get("avatarurl") or "",
                    "content": raw.get("rootcommentcontent") or raw.get("content") or "",
                    "likes": raw.get("praisenum") or 0,
                    "time": raw.get("time") or 0,
                })
        total = int(((data.get("comment") or {}).get("commenttotal")) or 0)
        return {"items": rows, "total": total, "provider": self.name}

    def resolve(self, item: dict) -> dict:
        mid = item.get("mid") or item.get("id")
        uin = self._cookie_value(self.cookie, "uin", "qqmusic_uin", "wxuin")
        if uin.startswith("o"):
            uin = uin[1:]
        authst = self._cookie_value(self.cookie, "qqmusic_key", "qm_keyst")
        param = {"guid": "10000", "songmid": [mid], "songtype": [0],
                 "uin": uin or "0", "loginflag": 1, "platform": "20"}
        if authst:
            param["authst"] = authst
        body = {
            "req_0": {
                "module": "music.vkey.GetVkey",
                "method": "UrlGetVkey",
                "param": param,
            },
            "comm": {"uin": int(uin) if uin.isdigit() else 0,
                     "format": "json", "ct": 24, "cv": 0},
        }
        data = _json("https://u.y.qq.com/cgi-bin/musicu.fcg",
                     self._headers(**{"Content-Type": "application/json"}),
                     json.dumps(body).encode())
        block = (data.get("req_0") or {}).get("data") or {}
        info = (block.get("midurlinfo") or [{}])[0]
        if not info.get("purl"):
            raise ProviderError("QQ 音乐没有返回直链（可能需要会员或该曲无版权）")
        sip = (block.get("sip") or ["https://dl.stream.qqmusic.qq.com/"])[0]
        url = sip + info["purl"]
        filename = str(info.get("filename") or "")
        ext = "m4a" if ".m4a" in filename else "mp3"
        quality = "320k" if "M500" in filename else ("128k" if not authst else "会员")
        return {"url": url, "ext": ext, "quality": quality,
                "headers": self._headers(), "provider": self.name}


# ---------------------------------------------------------------------------
# 酷狗
# ---------------------------------------------------------------------------

_KG_API = "http://mobilecdn.kugou.com/api/v3"


class KugouProvider(Provider):
    name = "kugou"
    label = "酷狗"
    referer = "https://www.kugou.com/"

    def _song(self, raw: dict) -> dict:
        hash_value = raw.get("hash") or raw.get("FileHash") or ""
        album_id = raw.get("album_id") or raw.get("AlbumID") or ""
        title = raw.get("songname") or raw.get("SongName") or ""
        singer = raw.get("singername") or raw.get("SingerName") or ""
        filename = str(raw.get("filename") or raw.get("FileName") or "")
        if not title and filename:
            if " - " in filename:
                left, right = filename.split(" - ", 1)
                singer = singer or left.strip()
                title = right.strip()
            else:
                title = filename
        duration = raw.get("duration") or raw.get("Duration") or raw.get("timeLength") or 0
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            duration = 0
        return song(
            self.name, hash_value, title, singer,
            album=raw.get("album_name") or raw.get("AlbumName") or "",
            album_id=album_id, duration=duration,
            ext="mp3", mid=str(hash_value),
            quality="320k" if raw.get("320hash") or raw.get("HQFileHash") else "128k",
        )

    def cover(self, item: dict) -> str:
        url = super().cover(item)
        if url:
            return url
        hash_value = str(item.get("id") or "")
        if not hash_value:
            return ""
        try:
            data = _json(f"http://m.kugou.com/app/i/getSongInfo.php?cmd=playInfo"
                         f"&hash={urllib.parse.quote(hash_value)}", self._headers())
        except ProviderError:
            return ""
        return str(data.get("album_img") or "")

    def search(self, keyword: str, page: int = 1, limit: int = 30) -> list[dict]:
        limit = max(1, min(50, int(limit or 30)))
        page = max(1, int(page or 1))
        url = (f"{_KG_API}/search/song?format=json&keyword={urllib.parse.quote(keyword)}"
               f"&page={page}&pagesize={limit}&showtype=1")
        data = _json(url, self._headers())
        return [self._song(raw) for raw in (data.get("data") or {}).get("info") or []]

    def _collection(self, url: str, kind: str, cid: str) -> dict:
        data = _json(url, self._headers())
        block = data.get("data") or {}
        items = [self._song(raw) for raw in block.get("info") or []]
        if not items:
            raise ProviderError("酷狗没有这个内容")
        info: dict = {}
        info_api = {
            "album": f"{_KG_API}/album/info?albumid={urllib.parse.quote(str(cid))}&format=json",
            "playlist": f"{_KG_API}/special/info?specialid={urllib.parse.quote(str(cid))}&format=json",
            "rank": f"{_KG_API}/rank/info?rankid={urllib.parse.quote(str(cid))}&format=json",
        }.get(kind)
        if info_api:
            try:
                info = _json(info_api, self._headers()).get("data") or {}
            except ProviderError:
                info = {}
        title = (info.get("albumname") or info.get("specialname")
                 or info.get("rankname") or block.get("album_name")
                 or block.get("specialname") or block.get("rankname")
                 or f"酷狗 {cid}")
        cover = info.get("imgurl") or block.get("imgurl") or block.get("banner") or ""
        artist = info.get("singername") or info.get("nickname") or ""
        if kind == "album":
            for item in items:
                if not item.get("album"):
                    item["album"] = title
        return self.collection(title, cover, artist, items, kind=kind,
                               id=str(cid), provider=self.name)

    def album(self, album_id: str) -> dict:
        return self._collection(
            f"{_KG_API}/album/song?albumid={urllib.parse.quote(str(album_id))}"
            "&page=1&pagesize=-1&format=json", "album", album_id)

    def playlist(self, playlist_id: str) -> dict:
        return self._collection(
            f"{_KG_API}/special/song?specialid={urllib.parse.quote(str(playlist_id))}"
            "&page=1&pagesize=-1&format=json", "playlist", playlist_id)

    def rank(self, rank_id: str) -> dict:
        return self._collection(
            f"{_KG_API}/rank/song?rankid={urllib.parse.quote(str(rank_id))}"
            "&page=1&pagesize=100&format=json", "rank", rank_id)

    def lyrics(self, item: dict) -> dict:
        hash_value = item.get("id") or item.get("mid")
        duration = int(float(item.get("duration") or 0) * 1000)
        search = _json(
            "http://krcs.kugou.com/search?ver=1&man=yes&client=mobi"
            f"&keyword={urllib.parse.quote(str(item.get('title') or ''))}"
            f"&duration={duration}&hash={urllib.parse.quote(str(hash_value))}",
            self._headers())
        candidates = search.get("candidates") or []
        if not candidates:
            raise ProviderError("酷狗没有找到歌词")
        first = candidates[0]
        data = _json(
            "http://lyrics.kugou.com/download?ver=1&client=pc"
            f"&id={urllib.parse.quote(str(first.get('id')))}"
            f"&accesskey={urllib.parse.quote(str(first.get('accesskey')))}"
            "&fmt=lrc&charset=utf8", self._headers())
        content = data.get("content") or ""
        try:
            text = base64.b64decode(content).decode("utf-8", "replace")
        except (ValueError, TypeError):
            text = ""
        if not text.strip():
            raise ProviderError("酷狗歌词内容为空")
        return {"lrc": text, "translated": ""}

    def comments(self, item: dict, page: int = 1, limit: int = 20) -> dict:
        return {"items": [], "total": 0, "provider": self.name,
                "note": "酷狗评论需要登录，暂不提供"}

    def resolve(self, item: dict) -> dict:
        hash_value = str(item.get("id") or item.get("mid") or "")
        if not hash_value:
            raise ProviderError("酷狗缺少 hash")
        data = _json(f"http://m.kugou.com/app/i/getSongInfo.php?cmd=playInfo"
                     f"&hash={urllib.parse.quote(hash_value)}", self._headers())
        url = str(data.get("url") or "").strip()
        if not url:
            for backup in data.get("backup_url") or []:
                if isinstance(backup, str) and backup.startswith("http"):
                    url = backup
                    break
        if not url:
            raise ProviderError("酷狗没有返回直链（可能需要登录或该曲无版权）")
        return {"url": url, "ext": "mp3",
                "quality": f"{int((data.get('bitRate') or 0))}k" if data.get("bitRate") else "128k",
                "headers": self._headers(), "provider": self.name}


# ---------------------------------------------------------------------------
# 酷我
# ---------------------------------------------------------------------------

_KW_SEARCH = "http://search.kuwo.cn/r.s"


class KuwoProvider(Provider):
    name = "kuwo"
    label = "酷我"
    referer = "http://www.kuwo.cn/"
    supports_album = False
    supports_playlist = False
    supports_rank = False

    def _song(self, raw: dict) -> dict:
        rid = str(raw.get("MUSICRID") or raw.get("DC_TARGETID") or "")
        if rid.startswith("MUSIC_"):
            rid = rid[6:]
        duration = raw.get("DURATION") or 0
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            duration = 0
        formats = str(raw.get("FORMATS") or "")
        return song(
            self.name, rid, raw.get("SONGNAME") or "", raw.get("ARTIST") or "",
            album=raw.get("ALBUM") or "", album_id=raw.get("ALBUMID") or "",
            duration=duration,
            cover=(str(raw.get("web_albumpic_short") or "").replace("120", "500")
                   if raw.get("web_albumpic_short") else ""),
            quality="320k" if "MP3H" in formats else "128k",
        )

    def search(self, keyword: str, page: int = 1, limit: int = 30) -> list[dict]:
        limit = max(1, min(50, int(limit or 30)))
        page = max(0, int(page or 1) - 1)
        url = (f"{_KW_SEARCH}?all={urllib.parse.quote(keyword)}&ft=music"
               f"&itemset=web_2013&client=kt&pn={page}&rn={limit}"
               "&rformat=json&encoding=utf8")
        text = _text(url, self._headers())
        try:
            data = json.loads(text)
        except ValueError:
            # 酷我返回的是单引号“JSON”，ast.literal_eval 才是对的钥匙
            try:
                data = ast.literal_eval(text)
            except (ValueError, SyntaxError) as exc:
                raise ProviderError(f"酷我返回格式异常：{exc}") from exc
        if not isinstance(data, dict):
            raise ProviderError("酷我返回格式异常")
        return [self._song(raw) for raw in data.get("abslist") or []]

    def lyrics(self, item: dict) -> dict:
        data = _json("http://m.kuwo.cn/newh5/singles/songinfoandlrc"
                     f"?musicId={urllib.parse.quote(str(item.get('id') or ''))}",
                     self._headers())
        lines = ((data.get("data") or {}).get("lrclist")) or []
        if not lines:
            raise ProviderError("这首歌没有歌词")
        out = []
        for row in lines:
            try:
                seconds = float(row.get("time") or 0)
            except (TypeError, ValueError):
                seconds = 0.0
            text = str(row.get("lineLyric") or "")
            out.append(f"[{int(seconds // 60):02d}:{seconds % 60:05.2f}]{text}")
        return {"lrc": "\n".join(out), "translated": ""}

    def comments(self, item: dict, page: int = 1, limit: int = 20) -> dict:
        return {"items": [], "total": 0, "provider": self.name,
                "note": "酷我评论接口需要网页会话，暂不提供"}

    def resolve(self, item: dict) -> dict:
        rid = str(item.get("id") or "")
        url = (f"http://antiserver.kuwo.cn/anti.s?type=convert_url&format=mp3"
               f"&response=url&rid=MUSIC_{urllib.parse.quote(rid)}")
        text = _text(url, self._headers()).strip().splitlines()[0].strip()
        if not text.startswith("http"):
            raise ProviderError("酷我没有返回直链（该曲可能无版权）")
        return {"url": text, "ext": "mp3", "quality": item.get("quality") or "128k",
                "headers": self._headers(), "provider": self.name}


# ---------------------------------------------------------------------------
# 注册表 / 排行榜
# ---------------------------------------------------------------------------

PROVIDERS: dict[str, type[Provider]] = {
    NeteaseProvider.name: NeteaseProvider,
    QQProvider.name: QQProvider,
    KugouProvider.name: KugouProvider,
    KuwoProvider.name: KuwoProvider,
}

RANKS: dict[str, list[tuple[str, str]]] = {
    "netease": [("飙升榜", "19723756"), ("新歌榜", "3779629"),
                ("热歌榜", "3778678"), ("原创榜", "2884035")],
    "qq": [("热歌榜", "26"), ("新歌榜", "27"), ("飙升榜", "62"), ("原创榜", "58")],
    "kugou": [("TOP500", "8888"), ("飙升榜", "6666"), ("新歌榜", "23784")],
    "kuwo": [],
}

_CACHE: dict[str, tuple[float, object]] = {}
_CACHE_TTL = 300


def get(name: str, cfg: dict | None = None) -> Provider:
    provider = PROVIDERS.get(str(name or "").strip())
    if provider is None:
        raise ProviderError(f"未知音乐平台：{name}")
    return provider(cfg)


def describe() -> list[dict]:
    rows = []
    for name, cls in PROVIDERS.items():
        rows.append({
            "name": name,
            "label": cls.label,
            "album": cls.supports_album,
            "playlist": cls.supports_playlist,
            "rank": cls.supports_rank,
            "comments": cls.supports_comments,
            "ranks": [{"label": label, "id": rid} for label, rid in RANKS.get(name, [])],
        })
    return rows


def parse_collection_id(provider: str, text: str) -> str:
    """从链接或纯 ID 里抽出专辑/歌单 ID。"""
    text = str(text or "").strip()
    if not text:
        return ""
    if "://" not in text:
        return text
    parsed = urllib.parse.urlsplit(text)
    query = urllib.parse.parse_qs(parsed.query)
    if provider == "netease":
        for key in ("id", "albumid"):
            if query.get(key):
                return query[key][0]
        match = re.search(r"/(album|playlist|discover/toplist)/(\d+)", parsed.path)
        return match.group(2) if match else ""
    if provider == "qq":
        match = re.search(r"/(albumDetail|playlist|taoge)/([A-Za-z0-9]+)", parsed.path)
        if match:
            return match.group(2)
        if query.get("disstid"):
            return query["disstid"][0]
        if query.get("albummid"):
            return query["albummid"][0]
        return ""
    if provider == "kugou":
        match = re.search(r"/(album|special|rank)/(?:single/)?([A-Za-z0-9]+)", parsed.path)
        if match:
            return match.group(2)
        for key in ("albumid", "specialid", "rankid"):
            if query.get(key):
                return query[key][0]
        return ""
    if provider == "kuwo":
        match = re.search(r"/(album_detail|playlist_detail)/(\d+)", parsed.path)
        return match.group(2) if match else ""
    return ""


def cached(key: str, factory):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    value = factory()
    _CACHE[key] = (now, value)
    return value


__all__ = ["Provider", "ProviderError", "PROVIDERS", "RANKS", "cached",
           "describe", "get", "parse_collection_id", "song"]
