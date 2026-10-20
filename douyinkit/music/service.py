"""音乐下载队列：搜到的歌 → 直链 → 落盘 → 校验 → 转码 → 歌词。

和视频引擎分开：一首歌是一个轻量任务（几 MB、几秒），没必要复用视频那套
assets/steps。共用同一个 SQLite（`music_jobs` 表），重启后自动续跑。

设计上的几条硬规则：

* **下载完必须体检**：平台错误页、截断、0 字节都在这里拦下，坏文件直接删；
* **转换后还要再体检**：ffmpeg 成功退出不等于文件能播；
* **目标已存在且能播就跳过**，不重复下、不覆盖用户改过的文件；
* **取消要清干净**：`.part` 和转码临时文件都不留。
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from .. import media
from ..media import ffmpeg, verify
from ..store import Store
from . import ncm as ncm_mod
from . import providers, xlsx

_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]+')
_FORMATS = ("mp3", "wav", "flac", "original")
_BASE_ARGS: dict[str, tuple[str, ...]] = {
    "mp3": ("-c:a", "libmp3lame", "-id3v2_version", "3"),
    "wav": ("-c:a", "pcm_s16le"),
    "flac": ("-c:a", "flac"),
    "m4a": ("-c:a", "aac"),
    "aac": ("-c:a", "aac"),
    "ogg": ("-c:a", "libvorbis"),
    "opus": ("-c:a", "libopus"),
    "wma": ("-c:a", "wmav2"),
    "aiff": ("-c:a", "pcm_s16be"),
}
_QUALITY_ARGS: dict[str, dict[str, tuple[str, ...]]] = {
    "mp3": {"high": ("-b:a", "320k"), "standard": ("-b:a", "192k"),
            "low": ("-b:a", "128k")},
    "m4a": {"high": ("-b:a", "320k"), "standard": ("-b:a", "256k"),
            "low": ("-b:a", "128k")},
    "aac": {"high": ("-b:a", "320k"), "standard": ("-b:a", "256k"),
            "low": ("-b:a", "128k")},
    "ogg": {"high": ("-q:a", "8"), "standard": ("-q:a", "6"),
            "low": ("-q:a", "3")},
    "opus": {"high": ("-b:a", "192k"), "standard": ("-b:a", "160k"),
             "low": ("-b:a", "96k")},
    "wma": {"high": ("-b:a", "192k"), "standard": ("-b:a", "128k"),
            "low": ("-b:a", "96k")},
    "flac": {"high": ("-compression_level", "8"),
             "standard": ("-compression_level", "5"),
             "low": ("-compression_level", "2")},
}
_CODECS = {name: _BASE_ARGS[name] + _QUALITY_ARGS.get(name, {}).get("high", ())
           for name in _BASE_ARGS}
_CONVERT_FORMATS = tuple(_CODECS)
QUALITY_OPTIONS = [
    {"name": "high", "label": "高质量"},
    {"name": "standard", "label": "标准"},
    {"name": "low", "label": "省空间"},
]


def codec_args(fmt: str, quality: str = "high") -> list[str]:
    base = _BASE_ARGS.get(fmt)
    if base is None:
        raise MusicError(f"不支持的格式：{fmt}")
    args = list(base)
    preset = _QUALITY_ARGS.get(fmt) or {}
    args += list(preset.get(quality) or preset.get("high") or ())
    return args
_CONVERT_SUFFIXES = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".ape",
                     ".wma", ".aiff", ".opus")
_OP = {"mp3": "audio_mp3", "wav": "audio_wav", "flac": "audio_flac"}

CONVERT_FORMATS = [
    {"name": "mp3", "label": "MP3"},
    {"name": "wav", "label": "WAV 无损"},
    {"name": "flac", "label": "FLAC 无损"},
    {"name": "m4a", "label": "M4A (AAC)"},
    {"name": "aac", "label": "AAC"},
    {"name": "ogg", "label": "OGG Vorbis"},
    {"name": "opus", "label": "Opus"},
    {"name": "wma", "label": "WMA"},
    {"name": "aiff", "label": "AIFF 无损"},
]


class MusicError(RuntimeError):
    pass


class Cancelled(Exception):
    pass


class Paused(Exception):
    """暂停：保留 .part，继续时按 Range 续传。"""


def safe_name(name: str, limit: int = 90, fallback: str = "未命名") -> str:
    text = _BAD_NAME.sub("_", str(name or "")).strip(" .")
    text = re.sub(r"\s+", " ", text)
    if not text:
        text = fallback
    return text[:limit].strip(" .") or fallback


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def plan_target(root: Path, item: dict, extension: str) -> Path:
    artist = safe_name(item.get("artist") or "", 60, "未知歌手")
    album = safe_name(item.get("album") or "", 70, "")
    title = safe_name(item.get("title") or "", 100, "未命名")
    folder = root / artist / album if album else root / artist
    return folder / f"{title}.{extension.lstrip('.')}"


def collect_audio(path: str | Path) -> list[Path]:
    """把一个文件/文件夹展开成音频文件清单。"""
    source = Path(str(path or "")).expanduser()
    if source.is_file():
        return [source] if source.suffix.lower() in _CONVERT_SUFFIXES else []
    if source.is_dir():
        return sorted(
            item for item in source.rglob("*")
            if item.is_file() and item.suffix.lower() in _CONVERT_SUFFIXES
        )
    raise MusicError(f"路径不存在：{source}")


class MusicService:
    def __init__(self, store: Store, cfg: dict) -> None:
        self.store = store
        self.cfg = dict(cfg)
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._lock = threading.RLock()
        self._cancelled: set[str] = set()
        self._paused: set[str] = set()
        self._active: set[str] = set()
        self._closed = False
        self._workers: list[threading.Thread] = []
        pending = self.store.recover_music()
        self._spawn()
        for jid in pending:
            self._queue.put(jid)

    # ------------------------------------------------------------------ 生命周期

    def _spawn(self) -> None:
        count = max(1, min(6, int(self.cfg.get("music_concurrency") or 3)))
        for index in range(count):
            thread = threading.Thread(
                target=self._loop, name=f"music-{index}", daemon=True)
            thread.start()
            self._workers.append(thread)

    def apply_config(self, cfg: dict) -> None:
        self.cfg = dict(cfg)

    def shutdown(self) -> None:
        self._closed = True
        for _ in self._workers:
            self._queue.put("")
        for thread in self._workers:
            thread.join(timeout=1.5)

    def _loop(self) -> None:
        while not self._closed:
            jid = self._queue.get()
            if not jid or self._closed:
                continue
            try:
                self._run(jid)
            except Exception:  # noqa: BLE001 - 单曲失败不能带崩工作线程
                continue
            finally:
                self._queue.task_done()

    # ------------------------------------------------------------------ 提交

    def submit(self, items: list[dict], out_dir: str | None = None,
               fmt: str = "mp3", save_lyrics: bool = True,
               quality: str = "high") -> dict:
        fmt = (fmt or self.cfg.get("music_format") or "mp3").lower()
        if fmt not in _FORMATS:
            fmt = "mp3"
        quality = quality if quality in ("high", "standard", "low") else "high"
        root = Path(str(out_dir or self.cfg.get("music_dir") or "")).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        created = skipped = 0
        for item in items or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            provider = str(item.get("provider") or "netease")
            ext = str(item.get("ext") or "mp3").lower()
            extension = ext if fmt == "original" else fmt
            target = plan_target(root, item, extension)
            key = f"{provider}:{item.get('id')}:{target}"
            existing = self.store.find_music_job(key)
            if existing:
                if existing["status"] in ("queued", "running"):
                    skipped += 1
                    continue
                if existing["status"] == "done" and Path(existing["target"] or target).is_file():
                    if verify.check(existing["target"] or target).ok:
                        skipped += 1
                        continue
            if target.exists() and verify.check(target).ok:
                skipped += 1
                continue
            payload = json.dumps(
                {"kind": "song", "song": item, "fmt": fmt, "out_dir": str(root),
                 "save_lyrics": bool(save_lyrics), "target": str(target),
                 "quality": quality},
                ensure_ascii=False)
            jid = self.store.create_music_job(
                payload=payload, dedupe_key=key, provider=provider,
                song_id=str(item.get("id") or ""), title=item.get("title") or "",
                artist=item.get("artist") or "", album=item.get("album") or "",
                target=str(target),
            )
            self._queue.put(jid)
            created += 1
        return {"count": created, "skipped": skipped, "out_dir": str(root),
                "format": fmt}

    def submit_ncm(self, path: str, out_dir: str | None = None,
                   fmt: str = "mp3", quality: str = "high") -> dict:
        root = Path(str(out_dir or self.cfg.get("music_dir") or "")).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        fmt = fmt if fmt in (*_CODECS, "original") else "mp3"
        quality = quality if quality in ("high", "standard", "low") else "high"
        source = Path(str(path or "")).expanduser()
        if source.is_file():
            files = [source] if source.suffix.lower() == ".ncm" else []
        elif source.is_dir():
            files = sorted(source.rglob("*.ncm"))
        else:
            raise MusicError(f"路径不存在：{source}")
        if not files:
            raise MusicError("没有找到 .ncm 文件")
        created = skipped = 0
        for file in files:
            key = f"ncm:{file}:{fmt}:{quality}"
            existing = self.store.find_music_job(key)
            if existing and existing["status"] in ("queued", "running", "done"):
                skipped += 1
                continue
            payload = json.dumps({"kind": "ncm", "src": str(file), "fmt": fmt,
                                  "out_dir": str(root), "quality": quality},
                                 ensure_ascii=False)
            jid = self.store.create_music_job(
                payload=payload, dedupe_key=key, provider="ncm",
                song_id=str(file), title=file.stem, artist="", album="",
                target="", kind="ncm",
            )
            self._queue.put(jid)
            created += 1
        return {"count": created, "skipped": skipped, "total": len(files)}

    def submit_convert(self, jid: str, fmt: str = "mp3",
                       quality: str = "high") -> dict:
        """把一首已下载完成的歌转成指定格式（原文件保留）。"""
        fmt = str(fmt or "").lower()
        if fmt not in _CONVERT_FORMATS:
            raise MusicError(f"不支持的格式：{fmt or '空'}")
        quality = quality if quality in ("high", "standard", "low") else "high"
        job = self.store.get_music_job(str(jid))
        if not job:
            raise MusicError("任务不存在")
        if job["status"] != "done":
            raise MusicError("这首歌还没下载完成")
        source = Path(job.get("target") or "")
        if not source.is_file():
            raise MusicError("源文件已经不在了")
        if source.suffix.lower() == f".{fmt}":
            raise MusicError(f"已经是 {fmt.upper()}，不用再转")
        target = unique_path(source.with_suffix(f".{fmt}"))
        key = f"convert:{jid}:{fmt}:{target}:{quality}"
        existing = self.store.find_music_job(key)
        if existing and existing["status"] in ("queued", "running", "done"):
            return {"count": 0, "skipped": 1,
                    "target": existing.get("target") or str(target)}
        payload = json.dumps(
            {"kind": "convert", "src": str(source), "dst": str(target),
             "fmt": fmt, "title": job.get("title") or source.stem,
             "artist": job.get("artist") or "", "album": job.get("album") or "",
             "duration": 0, "quality": quality}, ensure_ascii=False)
        new_id = self.store.create_music_job(
            payload=payload, dedupe_key=key, provider="local",
            song_id=str(source), title=job.get("title") or source.stem,
            artist=job.get("artist") or "", album=job.get("album") or "",
            target=str(target), kind="convert",
        )
        self._queue.put(new_id)
        return {"count": 1, "skipped": 0, "target": str(target),
                "job_id": new_id}

    def submit_convert_local(self, path: str, fmt: str = "mp3",
                             out_dir: str | None = None,
                             quality: str = "high") -> dict:
        """本地文件/文件夹转码；默认写在原文件旁边。"""
        fmt = str(fmt or "").lower()
        if fmt not in _CONVERT_FORMATS:
            raise MusicError(f"不支持的格式：{fmt or '空'}")
        quality = quality if quality in ("high", "standard", "low") else "high"
        files = collect_audio(path)
        if not files:
            raise MusicError("没有找到可转换的音频（mp3/wav/flac/m4a/ogg…）")
        root = Path(str(out_dir or "")).expanduser() if str(out_dir or "").strip() else None
        if root is not None:
            root.mkdir(parents=True, exist_ok=True)
        created = skipped = 0
        for source in files:
            if root is not None:
                target = unique_path(root / f"{source.stem}.{fmt}")
            else:
                if source.suffix.lower() == f".{fmt}":
                    skipped += 1
                    continue
                target = unique_path(source.with_suffix(f".{fmt}"))
            if target == source:
                skipped += 1
                continue
            if target.exists() and verify.check(target).ok:
                skipped += 1
                continue
            key = f"convert-src:{source}:{target}:{quality}"
            existing = self.store.find_music_job(key)
            if existing and existing["status"] in ("queued", "running", "done"):
                skipped += 1
                continue
            payload = json.dumps(
                {"kind": "convert", "src": str(source), "dst": str(target),
                 "fmt": fmt, "title": source.stem, "artist": "", "album": "",
                 "duration": 0, "quality": quality}, ensure_ascii=False)
            new_id = self.store.create_music_job(
                payload=payload, dedupe_key=key, provider="local",
                song_id=str(source), title=source.stem, artist="", album="",
                target=str(target), kind="convert",
            )
            self._queue.put(new_id)
            created += 1
        return {"count": created, "skipped": skipped, "total": len(files),
                "format": fmt}

    # ------------------------------------------------------------------ 控制

    def cancel(self, jid: str) -> bool:
        job = self.store.get_music_job(jid)
        if not job or job["status"] in ("done", "failed", "cancelled"):
            return False
        status = job["status"]
        with self._lock:
            self._cancelled.add(jid)
            self._paused.discard(jid)
        if status in ("queued", "paused"):
            self.store.update_music_job(jid, status="cancelled", stage=None,
                                        error="已取消")
            partial = self._partial_path(job)
            if partial is not None:
                partial.unlink(missing_ok=True)
        return True

    def pause(self, jid: str) -> bool:
        """暂停：排队中的直接落状态；下载中的保留 .part，继续时续传。"""
        job = self.store.get_music_job(jid)
        if not job or job["status"] in ("done", "failed", "cancelled"):
            return False
        if job["status"] == "paused":
            return True
        if job["status"] == "queued":
            with self._lock:
                self._paused.add(jid)
            self.store.update_music_job(jid, status="paused", stage=None,
                                        error=None)
            return True
        if job["status"] == "running":
            with self._lock:
                self._paused.add(jid)
            return True
        return False

    def resume(self, jid: str) -> bool:
        job = self.store.get_music_job(jid)
        if not job or job["status"] != "paused":
            return False
        with self._lock:
            self._paused.discard(jid)
            self._cancelled.discard(jid)
        self.store.update_music_job(jid, status="queued", stage=None, error=None)
        self._queue.put(jid)
        return True

    @staticmethod
    def _partial_path(job: dict) -> Path | None:
        try:
            payload = json.loads(job.get("payload") or "{}")
        except ValueError:
            payload = {}
        raw = str(payload.get("target") or job.get("target") or "")
        if not raw.strip():
            return None
        target = Path(raw)
        return target.with_name(f"{target.stem}.{job.get('id')}.part{target.suffix}")

    def retry(self, jid: str) -> bool:
        job = self.store.get_music_job(jid)
        if not job or job["status"] not in ("failed", "cancelled"):
            return False
        with self._lock:
            self._cancelled.discard(jid)
        self.store.update_music_job(jid, status="queued", stage=None, error=None,
                                    progress=0.0)
        self._queue.put(jid)
        return True

    def retry_failed(self) -> int:
        count = 0
        for job in self.store.list_music_jobs(500):
            if job["status"] == "failed" and self.retry(job["id"]):
                count += 1
        return count

    def clear_finished(self) -> int:
        return self.store.clear_music_finished()

    def snapshot(self, limit: int = 200) -> dict:
        jobs = []
        for row in self.store.list_music_jobs(limit):
            payload = {}
            try:
                payload = json.loads(row.get("payload") or "{}")
            except ValueError:
                payload = {}
            row = dict(row)
            row.pop("payload", None)
            row["song"] = payload.get("song") or {}
            row["format"] = payload.get("fmt") or ""
            jobs.append(row)
        return {"jobs": jobs, "counts": self.store.music_counts(),
                "formats": [{"name": name, "label": label} for name, label in
                            (("mp3", "MP3 320k"), ("wav", "WAV 无损"),
                             ("flac", "FLAC 无损"), ("original", "保留原格式"))]}

    # ------------------------------------------------------------------ 执行

    def _cancelled_now(self, jid: str) -> bool:
        with self._lock:
            return jid in self._cancelled

    def _paused_now(self, jid: str) -> bool:
        with self._lock:
            return jid in self._paused

    def _run(self, jid: str) -> None:
        job = self.store.get_music_job(jid)
        if not job:
            return
        if job["status"] in ("done", "failed", "cancelled", "paused"):
            return
        with self._lock:
            if jid in self._active:
                # 暂停→继续的瞬间，旧线程可能还在收尾：稍后确认状态再入队
                timer = threading.Timer(0.4, self._requeue_if_queued, args=(jid,))
                timer.daemon = True
                timer.start()
                return
            self._active.add(jid)
        try:
            self._run_job(jid, job)
        finally:
            with self._lock:
                self._active.discard(jid)

    def _requeue_if_queued(self, jid: str) -> None:
        job = self.store.get_music_job(jid)
        if job and job["status"] == "queued":
            self._queue.put(jid)

    def _run_job(self, jid: str, job: dict) -> None:
        if self._cancelled_now(jid):
            self.store.update_music_job(jid, status="cancelled", stage=None)
            return
        try:
            payload = json.loads(job.get("payload") or "{}")
        except ValueError:
            payload = {}
        try:
            if payload.get("kind") == "ncm":
                self._run_ncm(jid, payload)
            elif payload.get("kind") == "convert":
                self._run_convert(jid, payload)
            else:
                self._run_song(jid, payload)
        except Paused:
            self.store.update_music_job(jid, status="paused", stage=None,
                                        error=None)
        except Cancelled:
            self.store.update_music_job(jid, status="cancelled", stage=None,
                                        error="已取消", progress=0)
        except (providers.ProviderError, ncm_mod.NcmError, media.TransformError,
                media.ToolMissing, MusicError) as exc:
            self.store.update_music_job(jid, status="failed", stage=None,
                                        error=str(exc)[:300])
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.store.update_music_job(jid, status="failed", stage=None,
                                        error=f"网络错误：{exc}"[:300])
        except Exception as exc:  # noqa: BLE001 - 兜底，别让任务卡在 running
            self.store.update_music_job(jid, status="failed", stage=None,
                                        error=f"{type(exc).__name__}: {exc}"[:300])
        finally:
            with self._lock:
                self._cancelled.discard(jid)

    def _stage(self, jid: str, stage: str, progress: float | None = None) -> None:
        fields: dict = {"stage": stage, "status": "running"}
        if progress is not None:
            fields["progress"] = max(0.0, min(1.0, progress))
        self.store.update_music_job(jid, **fields)

    def _run_song(self, jid: str, payload: dict) -> None:
        item = payload.get("song") or {}
        fmt = payload.get("fmt") or "mp3"
        out_dir = Path(payload.get("out_dir") or self.cfg.get("music_dir"))
        provider = providers.get(str(item.get("provider") or "netease"), self.cfg)

        self._stage(jid, "解析直链", 0.03)
        info = provider.resolve(item)
        ext = str(info.get("ext") or item.get("ext") or "mp3").lower()
        target = Path(payload.get("target") or plan_target(
            out_dir, item, ext if fmt == "original" else fmt))
        if fmt != "original":
            target = target.with_suffix(f".{fmt}")
        else:
            target = target.with_suffix(f".{ext}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target = unique_path(target)
        self.store.update_music_job(jid, target=str(target))
        cover = self._fetch_cover(item, target.parent, provider)

        temp = target.with_name(f"{target.stem}.{jid}.part{target.suffix}")
        paused = False
        try:
            self._download(jid, info, temp, target)
            if self._cancelled_now(jid):
                raise Cancelled()
            self._stage(jid, "校验音频", 0.6)
            check = verify.check(temp)
            if not check.ok:
                temp.unlink(missing_ok=True)
                raise MusicError(f"下载内容不是有效音频：{check.reason}")

            if fmt != "original" and ext != fmt:
                converted = self._convert(jid, temp, fmt, item, cover=cover,
                                          quality=payload.get("quality", "high"))
                target.parent.mkdir(parents=True, exist_ok=True)
                final = unique_path(target)
                converted.replace(final)
                target = final
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                final = unique_path(target)
                temp.replace(final)
                target = final
                self._tag_output(target, item, cover)

            self._stage(jid, "校验成品", 0.92)
            final_check = verify.check(target)
            if not final_check.ok:
                target.unlink(missing_ok=True)
                raise MusicError(f"成品校验失败：{final_check.reason}")

            if payload.get("save_lyrics", True):
                self._write_lyrics(provider, item, target)

            size = target.stat().st_size
            self.store.update_music_job(
                jid, status="done", stage=None, progress=1.0,
                bytes_total=size, bytes_done=size, target=str(target),
                error=None)
        except Paused:
            paused = True
            raise
        finally:
            if not paused:
                temp.unlink(missing_ok=True)

    def _download(self, jid: str, info: dict, temp: Path, target: Path) -> None:
        headers = {"User-Agent": providers.UA, **(info.get("headers") or {})}
        partial = temp.stat().st_size if temp.exists() else 0
        if partial:
            headers["Range"] = f"bytes={partial}-"
        request = urllib.request.Request(info["url"], headers=headers)
        self._stage(jid, "连接音源", 0.06)
        try:
            response = urllib.request.urlopen(request, timeout=30)
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            raise MusicError(f"下载失败：{exc}") from exc
        with response:
            ctype = (response.headers.get("Content-Type") or "").lower()
            if "text/html" in ctype or "json" in ctype:
                raise MusicError(f"音源返回了网页/JSON 而不是音频（{ctype}）")
            length = int(response.headers.get("Content-Length") or 0)
            resumed = partial > 0 and getattr(response, "status", 200) == 206
            if resumed:
                total = partial + length
                mode = "ab"
                done = partial
            else:
                total = length
                mode = "wb"
                done = 0
            last = 0.0
            temp.parent.mkdir(parents=True, exist_ok=True)
            with temp.open(mode) as handle:
                while True:
                    if self._cancelled_now(jid):
                        raise Cancelled()
                    if self._paused_now(jid):
                        raise Paused()
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if now - last >= 0.35:
                        last = now
                        fraction = (done / total) if total else min(0.55, done / 8_000_000)
                        self._stage(jid, f"下载 {done / 1048576:.1f} MB", 0.08 + 0.5 * fraction)
        if done <= 0:
            raise MusicError("音源返回 0 字节")
        self.store.update_music_job(jid, bytes_total=total or done, bytes_done=done)

    def _fetch_cover(self, item: dict, folder: Path,
                     provider=None) -> Path | None:
        url = str(item.get("cover") or "").strip()
        if not url and provider is not None:
            try:
                url = str(provider.cover(item) or "").strip()
            except Exception:  # noqa: BLE001 - 封面失败不影响下载
                url = ""
        if not url.startswith("http"):
            return None
        if "music.126.net" in url:
            # 网易云原图动辄 3000×3000 / 7 MB，取 600 的缩略图足够做封面
            url += ("&" if "?" in url else "?") + "param=600y600"
        target = Path(folder) / "cover.jpg"
        if target.is_file() and target.stat().st_size > 512:
            return target
        referer = {
            "netease": "https://music.163.com/",
            "qq": "https://y.qq.com/",
            "kugou": "https://www.kugou.com/",
            "kuwo": "http://www.kuwo.cn/",
        }.get(str(item.get("provider") or ""), "https://music.163.com/")
        request = urllib.request.Request(
            url, headers={"User-Agent": providers.UA, "Referer": referer})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                ctype = (response.headers.get("Content-Type") or "").lower()
                if "image" not in ctype:
                    return None
                data = response.read(8 * 1024 * 1024)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            return None
        if len(data) < 512:
            return None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except OSError:
            return None
        return target

    def _tag_output(self, path: Path, item: dict, cover: Path | None) -> None:
        """给不转码的产物补标签 / 封面；失败不影响下载结果。"""
        if path.suffix.lower() not in (".mp3", ".flac"):
            return
        metadata = [("title", item.get("title")), ("artist", item.get("artist")),
                    ("album", item.get("album"))]
        try:
            ffmpeg.retag(path, path, metadata, cover, self.cfg)
        except (media.ToolMissing, ffmpeg.ToolError, OSError):
            return

    def _convert(self, jid: str, source: Path, fmt: str, item: dict, *,
                 remove_source: bool = True, base: float = 0.6,
                 workdir: Path | None = None,
                 cover: Path | None = None,
                 quality: str = "high") -> Path:
        args = codec_args(fmt, quality)
        folder = Path(workdir) if workdir else source.parent
        folder.mkdir(parents=True, exist_ok=True)
        converted = folder / f"{source.stem}.{jid}.conv.{fmt}"
        meta = [("title", item.get("title")), ("artist", item.get("artist")),
                ("album", item.get("album"))]
        for key, value in meta:
            if value:
                args += ["-metadata", f"{key}={value}"]
        duration = float(item.get("duration") or 0)
        label = f"转换 {fmt.upper()}"

        def on_progress(fraction: float) -> None:
            if self._cancelled_now(jid):
                return
            self._stage(jid, f"{label} {fraction * 100:.0f}%",
                        base + 0.9 * (1 - base) * fraction)

        self._stage(jid, label, base)
        ffmpeg.run_audio(source, converted, args, duration, on_progress, self.cfg,
                         cover=cover if fmt in ("mp3", "flac") else None,
                         attached_pic=(fmt == "flac"))
        if remove_source:
            source.unlink(missing_ok=True)
        if self._cancelled_now(jid):
            converted.unlink(missing_ok=True)
            raise Cancelled()
        return converted

    def _write_lyrics(self, provider, item: dict, audio: Path) -> None:
        try:
            data = provider.lyrics(item)
        except Exception:  # noqa: BLE001 - 没歌词不算失败
            return
        text = (data.get("lrc") or "").strip()
        if not text:
            return
        translated = (data.get("translated") or "").strip()
        if translated:
            text += "\n\n" + translated
        try:
            audio.with_suffix(".lrc").write_text(text, encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------------ 本地转码

    def _run_convert(self, jid: str, payload: dict) -> None:
        source = Path(str(payload.get("src") or ""))
        target = Path(str(payload.get("dst") or ""))
        fmt = str(payload.get("fmt") or "mp3")
        if not source.is_file():
            raise MusicError(f"源文件不存在：{source}")
        check = verify.check(source)
        if not check.ok:
            raise MusicError(f"源文件无法播放：{check.reason}")
        item = {"title": payload.get("title") or source.stem,
                "artist": payload.get("artist") or "",
                "album": payload.get("album") or "",
                "duration": payload.get("duration") or check.duration or 0}
        self._stage(jid, f"转换 {fmt.upper()}", 0.03)
        cover = source.parent / "cover.jpg"
        converted = self._convert(jid, source, fmt, item,
                                  remove_source=False, base=0.05,
                                  workdir=target.parent,
                                  cover=cover if cover.is_file() else None,
                                  quality=payload.get("quality", "high"))
        target.parent.mkdir(parents=True, exist_ok=True)
        final = unique_path(target)
        converted.replace(final)
        final_check = verify.check(final)
        if not final_check.ok:
            final.unlink(missing_ok=True)
            raise MusicError(f"转换结果无法播放：{final_check.reason}")
        lyrics = source.with_suffix(".lrc")
        if lyrics.is_file():
            try:
                destination = final.with_suffix(".lrc")
                if not destination.exists():
                    destination.write_bytes(lyrics.read_bytes())
            except OSError:
                pass
        size = final.stat().st_size
        self.store.update_music_job(
            jid, status="done", stage=None, progress=1.0, target=str(final),
            bytes_total=size, bytes_done=size, error=None)

    # ------------------------------------------------------------------ NCM

    def _run_ncm(self, jid: str, payload: dict) -> None:
        src = Path(payload.get("src") or "")
        fmt = payload.get("fmt") or "mp3"
        out_dir = Path(payload.get("out_dir") or self.cfg.get("music_dir"))
        self._stage(jid, "解密 NCM", 0.1)
        temp_dir = out_dir / "_ncm_tmp" / jid
        temp_dir.mkdir(parents=True, exist_ok=True)

        def progress(fraction: float) -> None:
            self._stage(jid, f"解密 NCM {fraction * 100:.0f}%", 0.1 + 0.4 * fraction)

        exe = str(self.cfg.get("ncmc_path") or "").strip()
        try:
            result = ncm_mod.decode_file(src, temp_dir, on_progress=progress)
        except ncm_mod.NcmError:
            if not exe:
                raise
            result = ncm_mod.decode_with_ncmc(exe, src, temp_dir)
        decoded = Path(result["path"])
        meta = result.get("meta") or {}
        title = meta.get("title") or decoded.stem
        artist = meta.get("artist") or ""
        album = meta.get("album") or ""
        item = {"title": title, "artist": artist, "album": album,
                "duration": meta.get("duration") or 0}
        target = plan_target(out_dir, item, fmt if fmt != "original" else decoded.suffix.lstrip("."))
        target.parent.mkdir(parents=True, exist_ok=True)
        target = unique_path(target)
        self.store.update_music_job(jid, title=title, artist=artist, album=album,
                                    target=str(target))
        self._stage(jid, "整理音频", 0.55)
        cover_path = temp_dir / "cover.jpg"
        cover = cover_path if cover_path.is_file() else None
        converted = False
        if fmt != "original" and decoded.suffix.lower() != f".{fmt}":
            decoded = self._convert(jid, decoded, fmt, item, cover=cover,
                                    quality=payload.get("quality", "high"))
            converted = True
        if decoded != target:
            target.parent.mkdir(parents=True, exist_ok=True)
            decoded.replace(target)
        if not converted:
            self._tag_output(target, item, cover)
        cover = temp_dir / "cover.jpg"
        if cover.is_file():
            destination = target.parent / "cover.jpg"
            if not destination.exists():
                try:
                    destination.write_bytes(cover.read_bytes())
                except OSError:
                    pass
        check = verify.check(target)
        if not check.ok:
            target.unlink(missing_ok=True)
            raise MusicError(f"解密结果无法播放：{check.reason}")
        size = target.stat().st_size
        self.store.update_music_job(jid, status="done", stage=None, progress=1.0,
                                    bytes_total=size, bytes_done=size, error=None)
        try:
            import shutil

            shutil.rmtree(temp_dir, ignore_errors=True)
        except OSError:
            pass

    # ------------------------------------------------------------------ 导出

    def export(self, rows: list[list] | None = None, path: str | None = None) -> str:
        if rows is None:
            rows = [["歌名", "歌手", "专辑", "状态", "格式", "文件"]]
            for job in self.store.list_music_jobs(1000):
                fmt = ""
                try:
                    fmt = (json.loads(job.get("payload") or "{}") or {}).get("fmt") or ""
                except ValueError:
                    fmt = ""
                rows.append([job.get("title") or "", job.get("artist") or "",
                             job.get("album") or "", job.get("status") or "",
                             fmt, job.get("target") or ""])
        if path:
            target = Path(path).expanduser()
        else:
            root = Path(self.cfg.get("music_dir") or ".")
            root.mkdir(parents=True, exist_ok=True)
            target = root / f"音乐清单-{datetime.now():%Y%m%d-%H%M%S}.xlsx"
        xlsx.write_xlsx(target, rows, sheet_name="音乐", widths=[30, 22, 26, 10, 8, 60])
        return str(target)


__all__ = ["MusicService", "MusicError", "plan_target", "safe_name", "unique_path"]
