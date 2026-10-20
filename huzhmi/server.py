"""本地 HTTP 服务：静态页面 + JSON API。"""

from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from . import archive
from . import bridge as bridge_mod
from . import config as config_mod
from . import douyin
from . import media
from . import music as music_mod
from . import platforms
from . import rules as rules_mod
from .engine import Engine, free_space_gb
from .net import UA, PlatformError
from .paths import resource_dir
from .store import Store

# 打包后 web/ 在 _MEIPASS 里，开发时在仓库根目录
WEB_DIR = resource_dir() / "web"

# 封面代理允允许这些图床，避免变成任意 URL 转发器
THUMB_HOSTS = (
    "hdslb.com",
    "douyinpic.com",
    "douyinstatic.com",
    "byteimg.com",
    "pstatp.com",
    "bilivideo.com",
)


class App:
    """把 store / engine / 配置聚在一起，供 handler 使用。"""

    def __init__(self) -> None:
        cfg = config_mod.load()
        Path(cfg["output_dir"]).mkdir(parents=True, exist_ok=True)
        Path(cfg["music_dir"]).mkdir(parents=True, exist_ok=True)
        self.store = Store(str(config_mod.DB_PATH))
        # 上次没跑完的任务：重启后自动接着下，不用一个个点重试
        pending = self.store.recover_interrupted()
        self.engine = Engine(self.store, cfg)
        # 音乐下载队列（和视频任务分表，互不干扰）
        self.music = music_mod.service.MusicService(self.store, cfg)
        # 网易云扫码登录（拿 MUSIC_U，会员曲/无损靠它）
        self.music_login = music_mod.login.NeteaseLogin()
        self.restart_requested = False
        self.last_ping = time.time()
        url = str(cfg.get("rules_url") or "").strip()
        if url:
            threading.Thread(target=self._refresh_rules, args=(url,),
                             name="rules", daemon=True).start()

    @staticmethod
    def _refresh_rules(url: str) -> None:
        current = rules_mod.load()
        if time.time() - float(current.get("updated_at") or 0) < 86400:
            return
        try:
            rules_mod.fetch(url)
        except Exception:  # noqa: BLE001 - 热更新失败不影响启动
            pass
        if pending:
            self.engine.requeue(pending)

    def reconfig(self, patch: dict) -> dict:
        cfg = config_mod.save(patch)
        self.engine.apply_config(cfg)
        self.music.apply_config(cfg)
        Path(cfg["output_dir"]).mkdir(parents=True, exist_ok=True)
        Path(cfg["music_dir"]).mkdir(parents=True, exist_ok=True)
        return cfg

    def snapshot(self, limit: int = 100) -> dict:
        jobs = self.store.list_jobs(limit)
        return {
            "version": __version__,
            "config": config_mod.load(),
            "counts": self.store.counts(),
            "jobs": jobs,
            "free_gb": free_space_gb(Path(config_mod.load()["output_dir"])),
            # 前端靠这份描述识别链接、取平台名和单页条数，不再硬编码
            "platforms": platforms.describe(),
            # 音乐平台能力（哪些支持专辑/歌单/评论、有哪些排行榜）
            "music_providers": music_mod.providers.describe(),
            "music": self.music.snapshot(60),
            "music_convert_formats": music_mod.service.CONVERT_FORMATS,
            "music_qualities": music_mod.service.QUALITY_OPTIONS,
            "music_login": self.music_login.account(
                config_mod.load().get("music_cookie_netease") or ""),
            "rules": rules_mod.load(),
            "queue_paused": self.engine.queue_paused,
            # 转换能力：哪些可用、为什么不可用
            "capabilities": media.describe(config_mod.load()),
        }


def thumb_target(query: dict) -> str:
    """封面代理要抓的目标地址。

    `parse_qs` 已经把参数 URL 解码过一次；这里**不能再 unquote**，否则
    抖音封面的签名参数（`x-expires` / `x-signature`）会被改坏、CDN 回 403。
    """
    return (query.get("url") or [""])[0]


def _json(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _text(handler: BaseHTTPRequestHandler, body: bytes, ctype: str,
          status: int = 200) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    handler.wfile.write(body)


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"Huzhmi/{__version__}"

        def log_message(self, fmt: str, *args) -> None:  # 静音，避免刷屏
            return

        # ---------------- GET ----------------

        def do_GET(self) -> None:  # noqa: N802
            if not self._guard():
                return
            try:
                self._handle_get()
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:  # noqa: BLE001 - 任何接口异常都要有 JSON
                try:
                    _json(self, {"error": f"内部错误：{type(exc).__name__}: {exc}"}, 500)
                except Exception:  # noqa: BLE001
                    pass

        def _guard(self) -> bool:
            """只允许本地页面访问；挡住 DNS rebinding / 跨站页面。"""
            host = (self.headers.get("Host") or "").split(":")[0].strip("[]").lower()
            if host and host not in ("127.0.0.1", "localhost", "::1"):
                _json(self, {"error": "forbidden host"}, 403)
                return False
            origin = self.headers.get("Origin") or ""
            if origin:
                name = (urllib.parse.urlsplit(origin).hostname or "").lower()
                if name not in ("127.0.0.1", "localhost", "::1"):
                    _json(self, {"error": "forbidden origin"}, 403)
                    return False
            return True

        def _handle_get(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)

            if path == "/api/health":
                return _json(
                    self,
                    {
                        "ok": True,
                        "version": __version__,
                        "bridge_ready": bridge_mod.Bridge._instance is not None,
                    },
                )
            if path == "/api/rules":
                return _json(self, rules_mod.load())
            if path == "/api/ping":
                app.last_ping = time.time()
                return _json(self, {"ok": True})
            if path == "/api/bootstrap":
                return _json(self, app.snapshot())
            if path == "/api/jobs":
                limit = int((query.get("limit") or ["100"])[0])
                return _json(self, {"jobs": app.store.list_jobs(max(1, min(500, limit)))})
            if path == "/api/thumb":
                return self._thumb(query)
            if path == "/api/music/login/status":
                name = str((query.get("provider") or ["netease"])[0])
                cookie = config_mod.load().get(f"music_cookie_{name}") or ""
                if name == "netease":
                    return _json(self, app.music_login.account(cookie))
                return _json(self, {"provider": name, "logged_in": bool(cookie),
                                    "vip": False})
            if path == "/api/discover/hot":
                limit = int((query.get("limit") or ["50"])[0])
                try:
                    rows = douyin.hot_board(max(1, min(100, limit)),
                                            str(config_mod.load().get("cookie") or ""))
                except Exception as exc:  # noqa: BLE001
                    return _json(self, {"error": str(exc)}, 502)
                return _json(self, {"items": rows})
            if path == "/api/music/queue":
                limit = int((query.get("limit") or ["200"])[0])
                return _json(self, app.music.snapshot(max(1, min(500, limit))))
            if path == "/api/music/stream":
                return self._stream(query)
            if path == "/api/archive":
                cfg = config_mod.load()
                try:
                    return _json(self, archive.scan(
                        cfg["output_dir"], str((query.get("q") or [""])[0]),
                        int((query.get("limit") or ["200"])[0]),
                        int((query.get("offset") or ["0"])[0])))
                except FileNotFoundError as exc:
                    return _json(self, {"error": str(exc)}, 404)
            if path == "/api/archive/file":
                raw = str((query.get("path") or [""])[0])
                target = Path(raw).expanduser().resolve() if raw else None
                base = Path(config_mod.load()["output_dir"]).resolve()
                if target is None or not (target == base or base in target.parents):
                    return _text(self, b"forbidden", "text/plain; charset=utf-8", 403)
                if not target.is_file():
                    return _text(self, b"not found", "text/plain; charset=utf-8", 404)
                return self._send_file(target)
            if path == "/api/music/library":
                cfg = config_mod.load()
                try:
                    return _json(self, music_mod.library.scan(
                        cfg["music_dir"], str((query.get("q") or [""])[0]),
                        int((query.get("limit") or ["200"])[0]),
                        int((query.get("offset") or ["0"])[0])))
                except FileNotFoundError as exc:
                    return _json(self, {"error": str(exc)}, 404)
            if path == "/api/music/library/lyrics":
                raw = str((query.get("path") or [""])[0])
                target = self._music_path(raw)
                if target is None:
                    return _json(self, {"error": "路径不在音乐目录内"}, 403)
                return _json(self, {"lrc": music_mod.library.lyrics_text(target)})
            if path == "/api/login/status":
                name = (query.get("platform") or [""])[0]
                platform = self._login_platform(name)
                if platform is None:
                    return _json(self, {"error": "未知平台"}, 400)
                return _json(self, self._platform_status(platform))
            if path.startswith("/api/login/") and path.endswith("/status"):
                name = path[len("/api/login/"):-len("/status")]
                platform = self._login_platform(name)
                if platform is None:
                    return _json(self, {"error": "未知平台"}, 400)
                return _json(self, self._platform_status(platform))
            # 兼容老前端的按钮路径
            if path == "/api/bili/login/status":
                return _json(self, self._platform_status(platforms.by_name("bilibili")))
            if path == "/api/douyin/login/status":
                return _json(self, self._platform_status(platforms.by_name("douyin")))
            if path.startswith("/api/jobs/"):
                jid = path.rsplit("/", 1)[-1]
                job = app.store.get_job(jid)
                if not job:
                    return _json(self, {"error": "任务不存在"}, 404)
                job["assets"] = app.store.get_assets(jid)
                job["steps"] = app.store.list_steps(jid)
                return _json(self, {"job": job})
            return self._static(path)

        def _static(self, path: str) -> None:
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            target = (WEB_DIR / rel).resolve()
            if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
                return _text(self, b"not found", "text/plain; charset=utf-8", 404)
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            return _text(self, target.read_bytes(), ctype)

        # ---------------- POST ----------------

        def do_POST(self) -> None:  # noqa: N802
            if not self._guard():
                return
            try:
                self._handle_post()
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:  # noqa: BLE001
                try:
                    _json(self, {"error": f"内部错误：{type(exc).__name__}: {exc}"}, 500)
                except Exception:  # noqa: BLE001
                    pass

        def _handle_post(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            payload = self._body()

            if path == "/api/jobs":
                text = payload.get("text") or ""
                if not str(text).strip():
                    return _json(self, {"error": "链接为空"}, 400)
                base = None
                out_dir = str(payload.get("out_dir") or "").strip()
                if out_dir:
                    try:
                        target = Path(out_dir).expanduser().resolve()
                        target.mkdir(parents=True, exist_ok=True)
                        base = str(target)
                    except OSError as exc:
                        return _json(self, {"error": f"下载目录不可用：{exc}"}, 400)
                return _json(self, {"results": app.engine.submit(
                    str(text), base, bool(payload.get("force")))})
            if path == "/api/profile":
                return self._profile(payload)
            if path == "/api/episodes":
                return self._episodes(payload)
            if path == "/api/resolve":
                return self._resolve(payload)
            if path.startswith("/api/login/") or path.startswith("/api/logout/"):
                return self._login_action(path)
            # 兼容老前端的按钮路径
            if path == "/api/bili/login":
                return _json(self, platforms.by_name("bilibili").start_login())
            if path == "/api/bili/logout":
                platforms.by_name("bilibili").logout()
                return _json(self, self._platform_status(platforms.by_name("bilibili")))
            if path == "/api/douyin/login":
                return _json(self, platforms.by_name("douyin").start_login())
            if path == "/api/douyin/logout":
                platforms.by_name("douyin").logout()
                return _json(self, self._platform_status(platforms.by_name("douyin")))
            if path == "/api/discover/search":
                keyword = str(payload.get("keyword") or "").strip()
                if not keyword:
                    return _json(self, {"error": "搜索词为空"}, 400)
                offset = int(payload.get("offset") or 0)
                count = int(payload.get("count") or 20)
                try:
                    data = bridge_mod.Bridge.get().search(keyword, offset, count)
                except bridge_mod.BridgeError as exc:
                    return _json(self, {"error": str(exc)}, 502)
                return _json(self, data)
            if path == "/api/profile/more":
                return self._profile_more(payload)
            if path == "/api/select":
                return self._select(payload)
            if path == "/api/queue":
                paused = app.engine.pause_queue(bool(payload.get("paused")))
                return _json(self, {"paused": paused})
            # ---------------- 音乐下载 ----------------
            if path == "/api/music/login/start":
                try:
                    return _json(self, app.music_login.start())
                except music_mod.login.LoginError as exc:
                    return _json(self, {"error": str(exc)}, 502)
            if path == "/api/music/login/poll":
                key = str(payload.get("key") or "")
                try:
                    result = app.music_login.poll(key)
                except music_mod.login.LoginError as exc:
                    return _json(self, {"error": str(exc)}, 502)
                if result.get("cookie"):
                    app.reconfig({"music_cookie_netease": result["cookie"]})
                    result["account"] = app.music_login.account(
                        result["cookie"], use_cache=False)
                    result.pop("cookie", None)
                return _json(self, result)
            if path == "/api/music/login/logout":
                name = str(payload.get("provider") or "netease")
                app.reconfig({f"music_cookie_{name}": ""})
                if name == "netease":
                    app.music_login._account_cache = None
                return _json(self, {"ok": True, "provider": name})
            if path == "/api/music/login/cookie":
                name = str(payload.get("provider") or "").strip()
                if name not in music_mod.providers.PROVIDERS:
                    return _json(self, {"error": f"未知平台：{name}"}, 400)
                cookie = " ".join(str(payload.get("cookie") or "").split())
                if name == "netease" and cookie:
                    account = app.music_login.account(cookie, use_cache=False)
                    if not account.get("logged_in"):
                        return _json(self, {"error": "网易云 Cookie 无效或已过期"}, 400)
                app.reconfig({f"music_cookie_{name}": cookie})
                if name == "netease":
                    app.music_login._account_cache = None
                    return _json(self, app.music_login.account(
                        cookie, use_cache=False))
                return _json(self, {"provider": name, "logged_in": bool(cookie),
                                    "vip": False})
            if path == "/api/music/search":
                keyword = str(payload.get("keyword") or "").strip()
                if not keyword:
                    return _json(self, {"error": "搜索词为空"}, 400)
                name = str(payload.get("provider") or "netease")
                page = int(payload.get("page") or 1)
                limit = int(payload.get("limit") or 30)
                try:
                    provider = music_mod.providers.get(name, config_mod.load())
                    items = provider.search(keyword, page, limit)
                except music_mod.providers.ProviderError as exc:
                    return _json(self, {"error": str(exc)}, 502)
                return _json(self, {"provider": name, "items": items,
                                    "page": page})
            if path == "/api/music/collection":
                name = str(payload.get("provider") or "netease")
                kind = str(payload.get("kind") or "album").lower()
                raw_id = str(payload.get("id") or "").strip()
                cid = music_mod.providers.parse_collection_id(name, raw_id)
                if not cid:
                    return _json(self, {"error": "专辑/歌单 ID 为空"}, 400)
                try:
                    provider = music_mod.providers.get(name, config_mod.load())
                    if kind == "playlist":
                        data = provider.playlist(cid)
                    elif kind == "rank":
                        data = provider.rank(cid)
                    else:
                        data = provider.album(cid)
                except music_mod.providers.ProviderError as exc:
                    return _json(self, {"error": str(exc)}, 502)
                return _json(self, data)
            if path == "/api/music/lyrics":
                item = payload.get("song") or {}
                try:
                    provider = music_mod.providers.get(
                        str(item.get("provider") or "netease"), config_mod.load())
                    data = provider.lyrics(item)
                except music_mod.providers.ProviderError as exc:
                    return _json(self, {"error": str(exc)}, 502)
                return _json(self, data)
            if path == "/api/music/comments":
                item = payload.get("song") or {}
                page = int(payload.get("page") or 1)
                limit = int(payload.get("limit") or 20)
                try:
                    provider = music_mod.providers.get(
                        str(item.get("provider") or "netease"), config_mod.load())
                    data = provider.comments(item, page, limit)
                except music_mod.providers.ProviderError as exc:
                    return _json(self, {"error": str(exc)}, 502)
                return _json(self, data)
            if path == "/api/music/download":
                items = payload.get("items") or []
                if not isinstance(items, list) or not items:
                    return _json(self, {"error": "没有选中的歌曲"}, 400)
                fmt = str(payload.get("format") or "mp3")
                out_dir = str(payload.get("out") or "").strip() or None
                save_lyrics = payload.get("lyrics", True) is not False
                quality = str(payload.get("quality") or "high")
                try:
                    return _json(self, app.music.submit(
                        items, out_dir, fmt, save_lyrics, quality))
                except OSError as exc:
                    return _json(self, {"error": f"目录不可用：{exc}"}, 400)
            if path == "/api/music/ncm":
                src = str(payload.get("path") or "").strip()
                if not src:
                    return _json(self, {"error": "NCM 路径为空"}, 400)
                fmt = str(payload.get("format") or "mp3")
                out_dir = str(payload.get("out") or "").strip() or None
                quality = str(payload.get("quality") or "high")
                try:
                    return _json(self, app.music.submit_ncm(
                        src, out_dir, fmt, quality))
                except music_mod.service.MusicError as exc:
                    return _json(self, {"error": str(exc)}, 400)
            if path == "/api/music/convert":
                jid = str(payload.get("id") or "").strip()
                fmt = str(payload.get("format") or "mp3").lower()
                quality = str(payload.get("quality") or "high")
                try:
                    return _json(self, app.music.submit_convert(jid, fmt, quality))
                except music_mod.service.MusicError as exc:
                    return _json(self, {"error": str(exc)}, 400)
            if path == "/api/music/convert-local":
                src = str(payload.get("path") or "").strip()
                if not src:
                    return _json(self, {"error": "路径为空"}, 400)
                fmt = str(payload.get("format") or "mp3").lower()
                out_dir = str(payload.get("out") or "").strip() or None
                quality = str(payload.get("quality") or "high")
                try:
                    return _json(self, app.music.submit_convert_local(
                        src, fmt, out_dir, quality))
                except music_mod.service.MusicError as exc:
                    return _json(self, {"error": str(exc)}, 400)
            if path == "/api/music/queue/cancel":
                ok = app.music.cancel(str(payload.get("id") or ""))
                return _json(self, {"ok": ok}, 200 if ok else 409)
            if path == "/api/music/queue/pause":
                ok = app.music.pause(str(payload.get("id") or ""))
                return _json(self, {"ok": ok}, 200 if ok else 409)
            if path == "/api/music/queue/resume":
                ok = app.music.resume(str(payload.get("id") or ""))
                return _json(self, {"ok": ok}, 200 if ok else 409)
            if path == "/api/music/queue/retry":
                ok = app.music.retry(str(payload.get("id") or ""))
                return _json(self, {"ok": ok}, 200 if ok else 409)
            if path == "/api/music/queue/retry-failed":
                return _json(self, {"count": app.music.retry_failed()})
            if path == "/api/music/queue/clear":
                return _json(self, {"count": app.music.clear_finished()})
            if path == "/api/music/library/delete":
                cfg = config_mod.load()
                root = Path(cfg["music_dir"]).resolve()
                target = self._music_path(str(payload.get("path") or ""))
                if target is None or not (target == root or root in target.parents):
                    return _json(self, {"error": "路径不在音乐目录内"}, 403)
                try:
                    ok = music_mod.library.delete(root, target)
                except OSError as exc:
                    return _json(self, {"error": f"删除失败：{exc}"}, 500)
                return _json(self, {"ok": ok}, 200 if ok else 404)
            if path == "/api/music/library/m3u":
                paths = payload.get("paths") or None
                if paths is not None and not isinstance(paths, list):
                    return _json(self, {"error": "paths 必须是数组"}, 400)
                cfg = config_mod.load()
                try:
                    result = music_mod.library.export_m3u(
                        cfg["music_dir"], [str(p) for p in (paths or [])] or None)
                except OSError as exc:
                    return _json(self, {"error": f"写入失败：{exc}"}, 500)
                return _json(self, {"path": result})
            if path == "/api/music/export":
                rows = payload.get("rows")
                if rows is not None:
                    if not isinstance(rows, list) or len(rows) > 5000:
                        return _json(self, {"error": "行数过多或格式不对"}, 400)
                    rows = [[cell for cell in (row or [])][:20]
                            for row in rows if isinstance(row, list)]
                try:
                    target = app.music.export(rows or None,
                                              str(payload.get("path") or "").strip() or None)
                except OSError as exc:
                    return _json(self, {"error": f"写不了文件：{exc}"}, 500)
                return _json(self, {"path": target})
            if path == "/api/music/plan":
                root = str(payload.get("path") or "").strip()
                out_root = str(payload.get("out") or root).strip()
                ext = str(payload.get("format") or "mp3").lower()
                if ext not in ("mp3", "wav", "flac", "m4a"):
                    return _json(self, {"error": f"不支持的格式：{ext}"}, 400)
                try:
                    return _json(self, media.library.plan(root, out_root, ext))
                except FileNotFoundError as exc:
                    return _json(self, {"error": str(exc)}, 404)
            if path == "/api/music/convert":
                root = str(payload.get("path") or "").strip()
                out_root = str(payload.get("out") or root).strip()
                ext = str(payload.get("format") or "mp3").lower()
                op = {"mp3": "audio_mp3", "wav": "audio_wav",
                      "flac": "audio_flac", "m4a": "audio_m4a"}.get(ext)
                if not op:
                    return _json(self, {"error": f"不支持的格式：{ext}"}, 400)
                try:
                    planned = media.library.plan(root, out_root, ext)
                except FileNotFoundError as exc:
                    return _json(self, {"error": str(exc)}, 404)
                count = app.engine.submit_library(planned["items"], op)
                return _json(self, {"count": count,
                                    "total": planned["total"],
                                    "broken": planned["broken"]})
            if path == "/api/music/scan":
                root = str(payload.get("path") or "").strip()
                if not root:
                    return _json(self, {"error": "目录为空"}, 400)
                try:
                    return _json(self, media.verify.scan(root))
                except FileNotFoundError as exc:
                    return _json(self, {"error": str(exc)}, 404)
            if path == "/api/music/check":
                target = str(payload.get("path") or "").strip()
                if not target:
                    return _json(self, {"error": "文件为空"}, 400)
                return _json(self, media.verify.check(target).as_dict())
            if path == "/api/jobs/retry-failed":
                return _json(self, {"count": app.engine.retry_failed()})
            if path == "/api/jobs/clear-finished":
                return _json(self, {"count": app.store.clear_finished()})
            if path == "/api/jobs/cancel-all":
                return _json(self, {"count": app.engine.cancel_all()})
            if path == "/api/config":
                return _json(self, {"config": app.reconfig(payload)})
            if path == "/api/rules":
                patch = payload.get("rules") if isinstance(payload.get("rules"), dict) else payload
                try:
                    return _json(self, rules_mod.save(patch))
                except (OSError, ValueError) as exc:
                    return _json(self, {"error": f"规则保存失败：{exc}"}, 400)
            if path == "/api/rules/fetch":
                url = str(payload.get("url") or "").strip()
                if not url.startswith(("http://", "https://")):
                    return _json(self, {"error": "规则地址必须是 http(s)"}, 400)
                try:
                    return _json(self, rules_mod.fetch(url))
                except Exception as exc:  # noqa: BLE001
                    return _json(self, {"error": f"规则更新失败：{exc}"}, 502)
            if path == "/api/restart":
                app.restart_requested = True
                return _json(self, {"ok": True})
            if path.startswith("/api/jobs/"):
                parts = path.strip("/").split("/")  # api/jobs/<id>/<action>
                if len(parts) == 4:
                    jid, action = parts[2], parts[3]
                    actions = {
                        "retry": app.engine.retry,
                        "cancel": app.engine.cancel,
                        "pause": app.engine.pause,
                        "resume": app.engine.resume,
                    }
                    if action == "transform":
                        op = str(payload.get("op") or "").strip()
                        try:
                            new_id = app.engine.submit_transform(jid, op)
                        except media.TransformError as exc:
                            return _json(self, {"error": str(exc)}, 400)
                        if not new_id:
                            return _json(
                                self,
                                {"error": "这个任务还不能转换（要已完成、文件还在，且没转过同一格式）"},
                                409,
                            )
                        return _json(self, {"job_id": new_id})
                    action_fn = actions.get(action)
                    if action_fn is not None:
                        ok = action_fn(jid)
                        return _json(self, {"ok": ok}, 200 if ok else 409)
            if path == "/api/reveal":
                return self._reveal(payload.get("path"))
            if path == "/api/archive/delete":
                cfg = config_mod.load()
                root = Path(cfg["output_dir"]).resolve()
                raw = str(payload.get("path") or "")
                target = Path(raw).expanduser().resolve() if raw else None
                if target is None or not (target == root or root in target.parents):
                    return _json(self, {"error": "路径不在下载目录内"}, 403)
                try:
                    ok = archive.delete(root, target)
                except OSError as exc:
                    return _json(self, {"error": f"删除失败：{exc}"}, 500)
                return _json(self, {"ok": ok}, 200 if ok else 404)
            return _json(self, {"error": "未知接口"}, 404)

        def _login_platform(self, name: str):
            """按名字取平台；认不出来返回 None。"""
            return platforms.find(name)

        def _platform_status(self, platform) -> dict:
            return platform.login_state(config_mod.load())

        def _login_action(self, path: str) -> None:
            """`/api/login/<平台>` / `/api/logout/<平台>`，平台自己知道怎么做登录。"""
            parts = [p for p in path.strip("/").split("/") if p]
            if len(parts) != 3:
                return _json(self, {"error": "未知接口"}, 404)
            _, action, name = parts
            platform = self._login_platform(name)
            if platform is None:
                return _json(self, {"error": f"未知平台：{name}"}, 400)
            if not platform.supports_login:
                return _json(self, {"error": f"{platform.label} 不支持登录"}, 400)
            if action == "login":
                return _json(self, platform.start_login())
            platform.logout()
            return _json(self, self._platform_status(platform))

        def _resolve(self, payload: dict) -> None:
            """把短链/分享链接展开，判断是主页还是单条作品。"""
            url = str(payload.get("url") or "").strip()
            if not url:
                return _json(self, {"error": "链接为空"}, 400)
            platform = platforms.resolve(url)
            try:
                final = platform.resolve_short(url)
            except Exception:  # noqa: BLE001
                final = url
            return _json(
                self,
                {
                    "kind": "profile" if platform.is_profile_url(final) else "video",
                    "platform": platform.name,
                    "url": final,
                },
            )

        def _episodes(self, payload: dict) -> None:
            """这条链接有没有「选集」（B 站分 P / 合集），有就列出来让用户勾。"""
            url = str(payload.get("url") or "").strip()
            if not url:
                return _json(self, {"error": "链接为空"}, 400)
            platform = platforms.resolve(url)
            try:
                data = platform.episodes(url, config_mod.load())
            except PlatformError as exc:
                return _json(self, {"error": str(exc)}, 502)
            return _json(self, data)

        def _profile(self, payload: dict) -> None:
            url = str(payload.get("url") or "").strip()
            if not url:
                return _json(self, {"error": "主页链接为空"}, 400)
            want = str(payload.get("platform") or "").strip()
            platform = platforms.find(want) or platforms.resolve(url)
            if not platform.is_profile_url(url):
                return _json(
                    self,
                    {"error": f"这不是 {platform.label} 的主页链接"},
                    400,
                )
            count = int(payload.get("count") or platform.page_size)
            try:
                data = platform.profile(url, count, config_mod.load())
            except PlatformError as exc:
                return _json(self, {"error": str(exc)}, 502)
            return _json(self, data)

        def _profile_more(self, payload: dict) -> None:
            token = str(payload.get("sec_uid") or "").strip()
            if not token:
                return _json(self, {"error": "缺少 sec_uid"}, 400)
            platform = platforms.find(str(payload.get("platform") or ""))
            if platform is None:
                return _json(self, {"error": "缺少或未知的平台"}, 400)
            cursor = int(payload.get("cursor") or 0)
            count = int(payload.get("count") or platform.page_size)
            try:
                page = platform.profile_more(token, cursor, count, config_mod.load())
            except PlatformError as exc:
                return _json(self, {"error": str(exc)}, 502)
            return _json(self, page)

        def _select(self, payload: dict) -> None:
            items = payload.get("items") or []
            if not isinstance(items, list) or not items:
                return _json(self, {"error": "没有选中的作品"}, 400)
            platform = str(payload.get("platform") or "douyin").strip()
            out_dir = str(payload.get("out_dir") or "").strip()
            base: str | None = None
            if out_dir:
                try:
                    target = Path(out_dir).expanduser().resolve()
                    target.mkdir(parents=True, exist_ok=True)
                    base = str(target)
                except OSError as exc:
                    return _json(self, {"error": f"下载目录不可用：{exc}"}, 400)
            return _json(
                self, {"results": app.engine.submit_items(items, base, platform)}
            )

        def _thumb(self, query: dict) -> None:
            """封面代理：抖音/B 站图床有防盗链，直接引用会被 403。"""
            url = thumb_target(query)
            host = urllib.parse.urlsplit(url).netloc.lower()
            if not any(host.endswith(h) for h in THUMB_HOSTS):
                return _text(self, b"forbidden host", "text/plain", 403)
            referer = (
                "https://www.bilibili.com/"
                if "hdslb" in host or "bilibili" in host
                else "https://www.douyin.com/"
            )
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": UA,
                        "Referer": referer,
                        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
                    },
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = resp.read(3_000_000)
                    ctype = resp.headers.get("Content-Type") or "image/jpeg"
            except Exception as exc:  # noqa: BLE001
                return _text(self, f"thumb failed: {exc}".encode(), "text/plain", 502)
            return _text(self, data, ctype)

        def _music_path(self, raw: str) -> Path | None:
            """校验一个路径是否在音乐目录 / 下载目录 / 已知音乐产物附近。"""
            raw = str(raw or "").strip()
            if not raw:
                return None
            cfg = config_mod.load()
            roots = [Path(cfg["music_dir"]).resolve(),
                     Path(cfg["output_dir"]).resolve()]
            for job in app.store.list_music_jobs(500):
                if job.get("target"):
                    roots.append(Path(job["target"]).resolve().parent)
            target = Path(raw).expanduser().resolve()
            if any(target == root or root in target.parents for root in roots):
                return target
            return None

        def _stream(self, query: dict) -> None:
            """给播放器用：支持 Range 的本地文件流（允允许音乐/下载目录）。"""
            cfg = config_mod.load()
            jid = (query.get("id") or [""])[0]
            raw = (query.get("path") or [""])[0]
            target: Path | None = None
            if jid:
                job = app.store.get_music_job(jid)
                if job and job.get("target"):
                    target = Path(job["target"])
            if target is None and raw:
                target = Path(raw)
            if target is None or not target.is_file():
                return _text(self, b"not found", "text/plain; charset=utf-8", 404)
            roots = [Path(cfg["music_dir"]).resolve(),
                     Path(cfg["output_dir"]).resolve()]
            for row in app.store.list_music_jobs(500):
                if row.get("target"):
                    roots.append(Path(row["target"]).resolve().parent)
            resolved = target.resolve()
            trusted = bool(jid) and any(
                row.get("id") == jid and row.get("target")
                and Path(row["target"]).resolve() == resolved
                for row in app.store.list_music_jobs(500))
            if not trusted and not any(
                    resolved == root or root in resolved.parents for root in roots):
                return _text(self, b"forbidden", "text/plain; charset=utf-8", 403)
            return self._send_file(resolved)

        def _send_file(self, resolved: Path) -> None:
            size = resolved.stat().st_size
            ctype = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
            start, end = 0, max(0, size - 1)
            status = 200
            header = self.headers.get("Range") or ""
            if header.startswith("bytes="):
                part = header[6:].split(",")[0].strip()
                try:
                    begin, finish = part.split("-", 1)
                    if begin:
                        start = int(begin)
                    if finish:
                        end = min(int(finish), size - 1)
                    if start > end or start >= size:
                        raise ValueError
                    status = 206
                except (ValueError, TypeError):
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            try:
                with resolved.open("rb") as handle:
                    handle.seek(start)
                    remaining = length
                    while remaining > 0:
                        block = handle.read(min(256 * 1024, remaining))
                        if not block:
                            break
                        self.wfile.write(block)
                        remaining -= len(block)
            except (BrokenPipeError, ConnectionResetError):
                return

        def _reveal(self, raw: str | None) -> None:
            cfg = config_mod.load()
            allowed_roots = [Path(cfg["output_dir"]).resolve(),
                             Path(cfg["music_dir"]).resolve()]
            for job in app.store.list_jobs(200):
                for key in ("out_dir", "out_base"):
                    if job.get(key):
                        allowed_roots.append(Path(job[key]).resolve())
            for job in app.store.list_music_jobs(500):
                if job.get("target"):
                    allowed_roots.append(Path(job["target"]).resolve().parent)
            target = Path(raw).resolve() if raw else allowed_roots[0]
            ok = any(target == root or root in target.parents for root in allowed_roots)
            if not ok:
                return _json(self, {"error": "路径不在下载目录内"}, 400)
            if not target.exists():
                return _json(self, {"error": "目录不存在"}, 404)
            try:
                if sys.platform == "win32":
                    os.startfile(str(target))  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(target)])
                else:
                    subprocess.Popen(["xdg-open", str(target)])
            except OSError as exc:
                return _json(self, {"error": f"打不开：{exc}"}, 500)
            return _json(self, {"ok": True})

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0:
                return {}
            raw = self.rfile.read(min(length, 4 * 1024 * 1024))
            try:
                data = json.loads(raw.decode("utf-8"))
                return data if isinstance(data, dict) else {}
            except (ValueError, UnicodeDecodeError):
                return {}

    return Handler


def build_server(host: str = "127.0.0.1", port: int = 8765,
                 retries: int = 12) -> tuple[ThreadingHTTPServer, int]:
    class LocalServer(ThreadingHTTPServer):
        # Windows 的 SO_REUSEADDR 允许两个进程绑同一端口（谁先收到包看运气），
        # 抖音客户端就占着 8901：如果允许复用，我们会「绑定成功」但请求全被它重置。
        allow_reuse_address = sys.platform != "win32"

    app = App()
    handler = make_handler(app)
    last_error: Exception | None = None
    for offset in range(retries):
        try:
            server = LocalServer((host, port + offset), handler)
            server.daemon_threads = True
            server.app = app  # type: ignore[attr-defined]
            return server, port + offset
        except OSError as exc:
            last_error = exc
    raise SystemExit(f"端口 {port}~{port + retries - 1} 都被占用：{last_error}")


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True,
          window: bool = False) -> bool:
    """启动服务，返回是否请求了重启。"""
    server, real_port = build_server(host, port)
    url = f"http://{host}:{real_port}/"
    print(f"Huzhmi {__version__} 已启动：{url}", flush=True)
    print(f"数据目录：{config_mod.DATA_DIR}", flush=True)

    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.4},
        name="http", daemon=True,
    )
    thread.start()

    proc = None
    if window:
        try:
            from . import window as window_mod

            proc = window_mod.open_window(url, config_mod.DATA_DIR / "window")
        except Exception as exc:  # noqa: BLE001 - 窗口开不了就退回浏览器
            print(f"独立窗口启动失败：{exc}", flush=True)

    if proc is not None:
        print("窗口已打开；关掉窗口即退出。", flush=True)
    elif open_browser:
        print("按 Ctrl+C 退出。", flush=True)
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    else:
        print("按 Ctrl+C 退出。", flush=True)

    try:
        if window or open_browser:
            # 页面每 5 秒 ping 一次；Edge 的启动进程提前退出时，只要窗口还
            # 在发心跳，服务就继续活着，不再跟着 launcher 一起退出。
            while time.time() - server.app.last_ping <= 25:  # type: ignore[attr-defined]
                if server.app.restart_requested:  # type: ignore[attr-defined]
                    if proc is not None:
                        try:
                            proc.terminate()
                        except OSError:
                            pass
                    server.shutdown()
                    break
                time.sleep(0.5)
        else:
            while thread.is_alive():
                if server.app.restart_requested:  # type: ignore[attr-defined]
                    server.shutdown()
                    break
                thread.join(0.5)
    except KeyboardInterrupt:
        print("\n正在退出…", flush=True)
    finally:
        server.shutdown()
        server.server_close()
        server.app.engine.shutdown()  # type: ignore[attr-defined]
        server.app.music.shutdown()   # type: ignore[attr-defined]
        # 关闭时按配置清理任务历史：允删记录，下载好的文件不动；暂停中的保留
        try:
            if config_mod.load().get("clear_history_on_exit", True):
                server.app.store.clear_finished()
                server.app.store.clear_music_finished()
        except Exception:  # noqa: BLE001
            pass
        server.app.store.close()      # type: ignore[attr-defined]
        bridge_mod.Bridge.shutdown()
    return bool(server.app.restart_requested)

