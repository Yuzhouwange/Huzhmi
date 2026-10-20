"""任务引擎：队列 + 线程池 + 下载 + 进度落库。"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import threading
import time
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config as config_mod
from . import media
from . import mp4mux
from . import platforms
from .net import PlatformError, guess_ext
from .store import Store

_URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&*+,;=%]+")
_TRAILING = ".,，。;；:：!！?？)）]】\"'》>"
# Windows 不允许的字符 + 控制字符（含 \x00-\x1f、DEL）
_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]+')
# 零宽 / 方向控制符：看着像乱码，其实不可见
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# 目录模板里的占位符 -> 展示名（平台名从注册表取，新增平台不用改这里）
_KIND_LABEL = {"video": "视频", "images": "图集", "live": "实况"}
# 默认不再给每条作品单开一层文件夹：平台/作者 之下直接放文件，文件名就是标题。
# 想看是谁下载的，文件夹名就是作者。
DEFAULT_TEMPLATE = "{platform}/{author}"
DEFAULT_FILE_TEMPLATE = "{title}"
# 老版本的默认值，升级时自动换掉（否则用户配置里还是套三层）
_LEGACY_TEMPLATES = ("{platform}/{author}/{title}", "{platform}/{kind}/{author}/{title}")


def platform_label(name: str) -> str:
    """平台展示名；认不出的名字原样返回，别把目录搞成空。"""
    platform = platforms.find(name)
    return platform.label if platform is not None else str(name or "")


class Cancelled(Exception):
    pass


class Paused(Exception):
    """暂停下载：保留已落盘的分片，恢复后接着下。"""


def extract_urls(text: str) -> list[str]:
    """从多行文本/分享文案里抽链接，去重保序。"""
    found = _URL_RE.findall(text or "")
    seen: set[str] = set()
    result: list[str] = []
    for url in found:
        url = url.rstrip(_TRAILING)
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


def safe_dirname(name: str, limit: int = 80, fallback: str = "") -> str:
    """把标题变成安全的 Windows 目录名。

    处理：非法字符、控制字符、零宽字符、首尾点/空格、设备保留名、
    孤立代理项（会写成乱码或被文件系统拒绝），以及清洗后没剩下有效信息的标题。
    """
    text = unicodedata.normalize("NFC", str(name or ""))
    text = _INVISIBLE.sub("", text)
    text = _BAD_NAME.sub("_", text)
    # 孤立代理项无法编码，直接丢掉，避免落盘时变成替换字符
    text = text.encode("utf-8", "ignore").decode("utf-8")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"_{2,}", "_", text)
    text = text.strip(" .")
    if limit > 0:
        text = text[:limit].strip(" .")
    # 全是 _ - . 空格，说明标题没剩下有效信息 -> 用兜底 ID
    if not text or not text.strip(" _-."):
        text = str(fallback or "untitled")
    if text.split(".")[0].upper() in _RESERVED:
        text = f"_{text}"
    return text


def _date_label(created) -> str:
    try:
        stamp = int(created or 0)
    except (TypeError, ValueError):
        stamp = 0
    if stamp <= 0:
        return time.strftime("%Y-%m-%d")
    try:
        return time.strftime("%Y-%m-%d", time.localtime(stamp))
    except (OSError, OverflowError, ValueError):
        return time.strftime("%Y-%m-%d")


def _template_labels(meta: dict, platform: str) -> dict:
    return {
        "platform": platform_label(str(platform or "douyin")),
        "author": meta.get("author") or "",
        "title": meta.get("title") or "",
        "kind": _KIND_LABEL.get(str(meta.get("kind") or "video"), "视频"),
        "id": str(meta.get("aweme_id") or ""),
        "date": _date_label(meta.get("created")),
    }


def build_out_dir(base: Path, meta: dict, platform: str, template: str) -> Path:
    """按模板生成归档目录。

    模板支持 {platform} {author} {title} {kind} {id} {date}，用 / 或 \\ 分层。
    默认 `{platform}/{author}`：平台 → 作者两层，作品直接以文件名落在作者目录里，
    不再为每条作品单独套一层文件夹。每一段都会单独做文件名清洗。
    """
    labels = _template_labels(meta, platform)

    parts: list[str] = []
    for chunk in re.split(r"[\\/]+", str(template or "")):
        if not chunk.strip():
            continue
        text = chunk
        for key, value in labels.items():
            text = text.replace("{" + key + "}", str(value))
        # 未知占位符整个删掉，不要留下 {foo} 或 foo 这种垃圾
        text = re.sub(r"\{[^}]*\}", "", text).strip()
        if text in ("", ".", ".."):
            continue
        parts.append(safe_dirname(text, limit=60))

    if not parts:
        parts = [
            safe_dirname(labels["platform"] or "downloads"),
            safe_dirname(labels["author"] or "unknown"),
            safe_dirname(labels["id"] or "untitled"),
        ]

    # 「下载到」里已经写了 平台/作者 时别再套一层：
    # 例如 base 已经以 抖音/rabbit 结尾，就不要再加一遍
    tail = [part.lower() for part in parts]
    existing = [part.lower() for part in base.parts[-len(tail):]]
    if len(tail) <= len(base.parts) and existing == tail:
        return base
    return base.joinpath(*parts)


def build_file_stem(meta: dict, platform: str, template: str,
                    index: int | None = None, total: int = 1) -> str:
    """按文件名模板生成文件名（不含扩展名）。

    默认 `{title}`；图集多张图或同一条作品有多个文件时，自动补 ` - 01`
    （除非模板里自己写了 `{index}`）。占位符和目录模板一致，另加 `{index}`。
    """
    labels = _template_labels(meta, platform)
    labels["index"] = f"{index:02d}" if index else ""

    text = str(template or "").strip() or DEFAULT_FILE_TEMPLATE
    for key, value in labels.items():
        text = text.replace("{" + key + "}", str(value))
    text = re.sub(r"\{[^}]*\}", "", text)
    text = re.sub(r"\s+", " ", text).strip(" .-_")
    stem = safe_dirname(text, limit=90, fallback=str(meta.get("aweme_id") or "video"))

    if total > 1 and index and "{index}" not in str(template or ""):
        stem = f"{stem} - {index:02d}"
    return stem


def unique_path(dest: Path, keep: set[Path] | None = None) -> Path:
    """文件名撞车时加 ` (2)`、` (3)`；`keep` 里的路径视为「本任务自己的」，直接复用。"""
    if keep and dest in keep:
        return dest
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    for counter in range(2, 1000):
        candidate = dest.with_name(f"{stem} ({counter}){suffix}")
        if keep and candidate in keep:
            return candidate
        if not candidate.exists():
            return candidate
    raise OSError(f"同名文件太多，无法命名：{dest.name}")


def normalize_any(url: str) -> tuple[str, str, str]:
    """返回 (干净链接, 去重键, 平台)。平台差异全部交给注册表。"""
    platform = platforms.resolve(url)
    clean, key = platform.normalize(url)
    return clean, key, platform.name


class Engine:
    def __init__(self, store: Store, cfg: dict) -> None:
        self.store = store
        self.cfg = dict(cfg)
        self._queue: queue.Queue[str] = queue.Queue()
        self._cancelled: set[str] = set()
        self._paused: set[str] = set()
        # 被「暂停全部」顺带停下的任务，恢复队列时只重新排队这些
        self._queue_paused_jobs: set[str] = set()
        # 每个任务正在写的临时文件（取消/暂停后要能找到并删掉）
        self._partials: dict[str, set[Path]] = {}
        # set = 允许派发；clear = 整个队列暂停
        self._queue_gate = threading.Event()
        self._queue_gate.set()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=self.cfg["concurrency"], thread_name_prefix="job"
        )
        threading.Thread(target=self._dispatch, name="dispatch", daemon=True).start()

    # ---------- 对外 ----------

    def submit(self, text: str, out_base: str | None = None,
               force: bool = False) -> list[dict]:
        results: list[dict] = []
        for url in extract_urls(text):
            clean, key, platform = normalize_any(url)
            done = None if force else self.store.find_done_by_key(key)
            if done:
                results.append(
                    {
                        "url": url,
                        "job_id": done["id"],
                        "status": "skipped",
                        "reason": "已下载过，跳过",
                    }
                )
                continue
            active = None if force else self.store.find_active_by_key(key)
            if active:
                results.append(
                    {
                        "url": url,
                        "job_id": active["id"],
                        "status": "skipped",
                        "reason": "已在队列 / 下载中，跳过",
                    }
                )
                continue
            jid = self.store.create_job(clean, key, out_base=out_base,
                                        platform=platform)
            self._queue.put(jid)
            results.append({"url": url, "job_id": jid, "status": "queued"})
        if not results:
            results.append(
                {"url": text[:120], "status": "error", "reason": "没找到链接"}
            )
        return results

    def submit_items(self, items: list[dict], out_base: str | None = None,
                     platform: str = "douyin") -> list[dict]:
        """主页多选：每个作品建一个任务。抖音直接带媒体地址，B 站下载时再解析。"""
        results: list[dict] = []
        for it in items:
            aweme_id = str(it.get("aweme_id") or "").strip()
            if not aweme_id:
                continue
            item_platform = str(it.get("platform") or platform or "douyin")
            target = platforms.find(item_platform) or platforms.resolve(
                str(it.get("source_url") or "")
            )
            # 作品链接和去重键都由平台自己拼，避免在这里写 bili:/aweme: 分支
            source_url = target.item_source_url(aweme_id)
            _, key = target.normalize(source_url)
            title = (it.get("desc") or "").strip() or aweme_id
            author = (it.get("author") or "").strip() or "unknown"

            done = self.store.find_done_by_key(key)
            if done:
                results.append(
                    {
                        "aweme_id": aweme_id,
                        "job_id": done["id"],
                        "status": "skipped",
                        "reason": "已下载过，跳过",
                    }
                )
                continue
            active = self.store.find_active_by_key(key)
            if active:
                results.append(
                    {
                        "aweme_id": aweme_id,
                        "job_id": active["id"],
                        "status": "skipped",
                        "reason": "已在队列 / 下载中，跳过",
                    }
                )
                continue

            assets: list[dict] = []
            play_url = (it.get("play_url") or "").strip()
            images = [u for u in (it.get("images") or []) if u]
            live = [u for u in (it.get("live") or []) if u]
            live_mode = str(self.cfg.get("live_photo") or "video")
            is_live = str(it.get("kind") or "").lower() == "live"
            if live and is_live and live_mode != "image":
                # 实况：默认只要那段 mp4；"both" 再加原图
                assets = [
                    {"idx": i, "index": i + 1, "kind": "video", "role": "live",
                     "url": url, "live": True}
                    for i, url in enumerate(live)
                ]
                if live_mode == "both":
                    assets += [
                        {"idx": len(assets) + i, "index": i + 1, "kind": "image",
                         "url": url}
                        for i, url in enumerate(images)
                    ]
            elif play_url and not images:
                # 图集千万不要用 play_url：图集作品的 video 是抖音自动生成的
                # 「图文 + 背景音乐」视频，存下来就只剩一段 BGM
                assets = [{"idx": 0, "kind": "video", "url": play_url}]
            elif images:
                assets = [
                    {"idx": i, "kind": "image", "url": url}
                    for i, url in enumerate(images)
                ]

            payload = None
            if assets:
                payload = json.dumps(
                    {
                        "aweme_id": aweme_id,
                        "title": title,
                        "author": author,
                        "kind": it.get("kind") or (
                        "live" if live else ("images" if images else "video")
                    ),
                        "assets": assets,
                        "source_url": source_url,
                        "author_id": str(
                            it.get("author_id") or it.get("sec_uid") or ""
                        ),
                        "created": int(
                            it.get("created") or it.get("create_time") or 0
                        ),
                        "platform": item_platform,
                    },
                    ensure_ascii=False,
                )

            jid = self.store.create_job(
                source_url, key, payload, out_base, platform=item_platform
            )
            self._queue.put(jid)
            results.append({"aweme_id": aweme_id, "job_id": jid, "status": "queued"})
        return results

    def retry(self, jid: str) -> bool:
        job = self.store.get_job(jid)
        if not job or job["status"] in ("queued", "running"):
            return False
        with self._lock:
            self._cancelled.discard(jid)
            self._paused.discard(jid)
        self.store.update_job(jid, status="queued", error=None, done_assets=0,
                              bytes_done=0)
        self._queue.put(jid)
        return True

    def requeue(self, jids: list[str]) -> int:
        """把一批任务放回队列（重启后恢复用）。"""
        count = 0
        for jid in jids:
            with self._lock:
                self._cancelled.discard(jid)
                self._paused.discard(jid)
            self._queue.put(jid)
            count += 1
        return count

    def submit_transform(self, jid: str, op: str) -> str | None:
        """给一个已完成的任务加一步「提取/转换」，生成子任务。"""
        job = self.store.get_job(jid)
        if not job or job["status"] != "done":
            return None
        transform = media.get(op)          # 未知类型会抛 TransformError
        asset = next(
            (a for a in self.store.get_assets(jid) if (a.get("path") or "").strip()),
            None,
        )
        if asset is None:
            return None
        src = Path(asset["path"])
        if not src.is_file():
            return None

        key = f"transform:{op}:{src}"
        if self.store.find_done_by_key(key):
            return None                     # 同一个文件同一个格式只转一次

        dst = unique_path(src.with_name(f"{src.stem}.{transform.extension}"))
        payload = json.dumps(
            {"transform": {"op": op, "src": str(src), "dst": str(dst)}},
            ensure_ascii=False,
        )
        new_id = self.store.create_job(
            f"transform:{op}/{src.name}", key, payload, str(src.parent),
            platform=job.get("platform") or "bilibili",
        )
        self.store.update_job(
            new_id,
            title=f"{job.get('title') or src.stem} · {transform.label}",
            author=job.get("author"),
            quality=transform.label,
            total_assets=1,
        )
        self.store.add_assets(
            new_id, [{"idx": 0, "kind": transform.kind, "url": src.name}]
        )
        self.store.add_step(new_id, 0, op, {"src": str(src), "dst": str(dst)})
        self._queue.put(new_id)
        return new_id

    def submit_convert(self, src: Path, dst: Path, op: str) -> str | None:
        """把一个文件转成目标路径（音乐库批量转换用），返回任务 id。"""
        src, dst = Path(src), Path(dst)
        if not src.is_file():
            return None
        key = f"convert:{op}:{src}"
        if self.store.find_done_by_key(key):
            return None
        payload = json.dumps(
            {"transform": {"op": op, "src": str(src), "dst": str(dst)}},
            ensure_ascii=False,
        )
        jid = self.store.create_job(
            f"convert:{op}/{src.name}", key, payload, str(dst.parent),
            platform="local",
        )
        self.store.update_job(
            jid, title=dst.stem, quality=media.get(op).label, total_assets=1
        )
        self.store.add_assets(jid, [{"idx": 0, "kind": "audio", "url": src.name}])
        self.store.add_step(jid, 0, op, {"src": str(src), "dst": str(dst)})
        self._queue.put(jid)
        return jid

    def submit_library(self, items: list[dict], op: str) -> int:
        """批量提交（每首一个任务，队列/暂停/取消都照旧生效）。"""
        count = 0
        for item in items:
            if not item.get("needs_convert"):
                continue
            if self.submit_convert(Path(item["source"]), Path(item["target"]), op):
                count += 1
        return count

    def _run_transform(self, job: dict, spec: dict, cfg: dict) -> None:
        """执行一步转换；进度写进 steps 表和任务进度条。"""
        jid = job["id"]
        op = str(spec.get("op") or "")
        src = Path(str(spec.get("src") or ""))
        dst = Path(str(spec.get("dst") or ""))
        self.store.update_job(jid, stage="准备")
        try:
            transform = media.get(op)
            self.store.update_step(jid, 0, status="running", progress=0.0)

            last = {"at": 0.0}

            def on_progress(fraction: float) -> None:
                self._check(jid)
                now = time.monotonic()
                if now - last["at"] < 0.4 and fraction < 1.0:
                    return
                last["at"] = now
                self.store.update_step(jid, 0, progress=round(fraction, 4))
                self.store.update_job(
                    jid, stage=f"{transform.label} {fraction * 100:.0f}%",
                    bytes_total=100, bytes_done=int(fraction * 100),
                )

            self.store.update_job(jid, stage=transform.label)
            stats = transform.run(src, dst, on_progress=on_progress, cfg=cfg)

            # 产出必须能播：平台错误页、截断、0 字节都在这里拦下
            check = media.verify.check(dst)
            if not check.ok:
                dst.unlink(missing_ok=True)
                raise media.TransformError(
                    f"输出校验失败：{check.reason}（已删除坏文件）"
                )
            size = int(stats.get("size") or dst.stat().st_size)
            self.store.update_asset(jid, 0, path=str(dst), size=size,
                                    status="done")
            self.store.update_step(jid, 0, status="done", progress=1.0,
                                   output_path=str(dst))
            self.store.update_job(
                jid, status="done", done_assets=1, bytes_total=size,
                bytes_done=size, stage=None, error=None,
            )
        except (Cancelled, Paused):
            raise
        except media.ToolMissing as exc:
            self.store.update_step(jid, 0, status="failed", error=str(exc))
            self.store.update_job(jid, status="failed", error=str(exc), stage=None)
        except media.TransformError as exc:
            self.store.update_step(jid, 0, status="failed", error=str(exc))
            self.store.update_job(jid, status="failed", error=str(exc), stage=None)
        except mp4mux.MuxError as exc:
            message = f"这个文件提取不了音频：{exc}"
            self.store.update_step(jid, 0, status="failed", error=message)
            self.store.update_job(jid, status="failed", error=message, stage=None)

    def retry_failed(self) -> int:
        """把所有失败的任务重新排队（重启后 99+ 条不用一个个点）。"""
        count = 0
        for job in self.store.list_jobs(1000):
            if job["status"] == "failed" and self.retry(job["id"]):
                count += 1
        return count

    def cancel(self, jid: str) -> bool:
        """取消任意未结束的任务；正在下载的会连带删掉已下载的部分。"""
        job = self.store.get_job(jid)
        if not job or job["status"] in ("done", "failed", "cancelled"):
            return False
        status = job["status"]

        if status == "paused":
            # 暂停中的任务没有线程在跑，直接落状态并清文件
            with self._lock:
                self._paused.discard(jid)
                self._cancelled.discard(jid)
            self.store.update_job(jid, status="cancelled", error="已取消",
                                  stage=None)
            self._discard_downloads(jid, job)
            return True

        if status == "queued":
            # 还在排队：不会有人去跑它，直接落状态
            with self._lock:
                self._cancelled.add(jid)
                self._paused.discard(jid)
            self.store.update_job(jid, status="cancelled", error="已取消",
                                  stage=None)
            self._discard_downloads(jid, job)
            return True

        # running：打标记，下载线程收尾时自己清理（见 _run 的 except Cancelled）
        with self._lock:
            self._cancelled.add(jid)
            self._paused.discard(jid)
        return True

    def cancel_all(self) -> int:
        """取消所有未结束的任务（排队 / 下载中 / 已暂停）。"""
        count = 0
        for job in self.store.list_jobs(1000):
            if job["status"] in ("queued", "running", "paused"):
                if self.cancel(job["id"]):
                    count += 1
        return count

    # ---------- 暂停 / 继续 ----------

    def pause(self, jid: str) -> bool:
        """暂停一条正在下载的任务：保留 .part，恢复时接着下。"""
        job = self.store.get_job(jid)
        if not job or job["status"] != "running":
            return False
        with self._lock:
            self._paused.add(jid)
        return True

    def resume(self, jid: str) -> bool:
        job = self.store.get_job(jid)
        if not job or job["status"] != "paused":
            return False
        with self._lock:
            self._paused.discard(jid)
        self.store.update_job(jid, status="queued", error=None, stage=None)
        self._queue.put(jid)
        return True

    def pause_queue(self, paused: bool) -> bool:
        """暂停/继续整个队列。

        暂停时不仅拦住新任务，也把**正在下载的**一起停下（「暂停全部」的字面意思）；
        继续时只把这些被队列暂停的任务重新排队，用户手动暂停的不动。
        """
        if paused:
            self._queue_gate.clear()
            running = [
                job["id"] for job in self.store.list_jobs(500)
                if job["status"] == "running"
            ]
            with self._lock:
                self._queue_paused_jobs = set(running)
                self._paused.update(running)
        else:
            self._queue_gate.set()
            with self._lock:
                targets = set(self._queue_paused_jobs)
                self._queue_paused_jobs = set()
                self._paused.difference_update(targets)
            if targets:
                threading.Thread(target=self._resume_settled, args=(targets,),
                                 name="resume", daemon=True).start()
        return not self._queue_gate.is_set()

    def _resume_settled(self, jids: set[str]) -> None:
        """等下载线程真正停下（状态落成 paused）再重新入队，避免同一任务跑两遍。"""
        pending = set(jids)
        deadline = time.time() + 30
        while pending and time.time() < deadline:
            for jid in list(pending):
                job = self.store.get_job(jid)
                if job is None or job["status"] in ("done", "failed", "cancelled"):
                    pending.discard(jid)
                elif job["status"] == "paused":
                    self.resume(jid)
                    pending.discard(jid)
            if pending:
                time.sleep(0.3)

    @property
    def queue_paused(self) -> bool:
        return not self._queue_gate.is_set()

    def apply_config(self, cfg: dict) -> None:
        """并发数变了要重建线程池；其余参数下一次取用时生效。"""
        rebuild = cfg.get("concurrency") != self.cfg.get("concurrency")
        self.cfg = dict(cfg)
        if rebuild:
            old = self._pool
            self._pool = ThreadPoolExecutor(
                max_workers=self.cfg["concurrency"], thread_name_prefix="job"
            )
            old.shutdown(wait=False, cancel_futures=False)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ---------- 内部 ----------

    def _dispatch(self) -> None:
        while True:
            jid = self._queue.get()
            # 队列暂停时卡在这里：任务留在手上但不会开始下载
            self._queue_gate.wait()
            try:
                self._pool.submit(self._run, jid)
            except RuntimeError:  # 线程池已关闭
                return

    def _is_cancelled(self, jid: str) -> bool:
        with self._lock:
            return jid in self._cancelled

    def _is_paused(self, jid: str) -> bool:
        with self._lock:
            return jid in self._paused

    def _register_partials(self, jid: str, *paths: Path) -> None:
        with self._lock:
            self._partials.setdefault(jid, set()).update(paths)

    def _forget_partials(self, jid: str) -> None:
        with self._lock:
            self._partials.pop(jid, None)

    def _discard_downloads(self, jid: str, job: dict) -> int:
        """删掉这个任务已经下载/下载了一半的文件，返回释放的字节数。"""
        with self._lock:
            paths = set(self._partials.pop(jid, set()))
        for asset in self.store.get_assets(jid):
            recorded = (asset.get("path") or "").strip()
            if recorded:
                paths.add(Path(recorded))

        # 兜底：同目录下和任务标题同名、以临时后缀结尾的分片
        # （比如程序重启过，内存里没有登记信息）
        out_dir = (job.get("out_dir") or "").strip()
        title = (job.get("title") or "").strip()
        if out_dir and title:
            base = Path(out_dir)
            if base.is_dir():
                prefix = title[:20]
                for path in base.iterdir():
                    if (path.is_file() and path.suffix in (".part", ".vpart", ".apart")
                            and path.name.startswith(prefix)):
                        paths.add(path)

        freed = 0
        for path in paths:
            try:
                if path.is_file():
                    freed += path.stat().st_size
                    path.unlink()
            except OSError:
                pass
        return freed

    def _check(self, jid: str) -> None:
        """下载循环里的中断点：取消和暂停都从这里抛出去。"""
        with self._lock:
            if jid in self._cancelled:
                raise Cancelled()
            if jid in self._paused:
                raise Paused()

    def _platform_for(self, job: dict):
        """任务属于哪个平台：先看入库时记下的名字，再看链接。"""
        return (
            platforms.find(job.get("platform"))
            or platforms.resolve(job.get("url") or "")
        )

    def _extract(self, job: dict, cfg: dict, fallback: bool = False) -> dict:
        """解析任务；`fallback=True` 走平台的降级解析（B 站：不要 DASH）。"""
        platform = self._platform_for(job)
        if fallback:
            return platform.extract_fallback(job["url"], cfg)
        return platform.extract(job["url"], cfg)

    def _run(self, jid: str) -> None:
        job = self.store.get_job(jid)
        if not job:
            return
        if self._is_cancelled(jid):
            # 排队期间就被取消了，别再解析/下载
            self.store.update_job(jid, status="cancelled", error="已取消",
                                  stage=None)
            with self._lock:
                self._cancelled.discard(jid)
            return
        self.store.update_job(jid, status="running", error=None)
        try:
            # 用引擎手里的配置（/api/config 改了会走 apply_config 同步过来）。
            # 不从磁盘重读：否则测试注入的 cfg 会被忽略，任务写到真实下载目录去。
            cfg = dict(self.cfg)
            payload = job.get("payload")
            if payload:
                spec = json.loads(payload)
                if isinstance(spec, dict) and spec.get("transform"):
                    # 转换任务：不碰平台解析，直接处理本地文件
                    self._run_transform(job, spec["transform"], cfg)
                    return
            if payload:
                # 主页多选：地址已在入库时解析好，直接用
                meta = json.loads(payload)
            else:
                meta = self._extract(job, cfg)

            base = Path(job.get("out_base") or cfg["output_dir"])
            platform = (meta.get("platform") or job.get("platform")
                        or platforms.resolve(job.get("url") or "").name)
            template = cfg.get("folder_template") or DEFAULT_TEMPLATE
            out_dir = build_out_dir(base, meta, platform, template)
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                # 路径过长 / 字符仍不合法：退化成「平台/作者/作品ID」
                out_dir = build_out_dir(
                    base, meta, platform, "{platform}/{author}/{id}"
                )
                out_dir.mkdir(parents=True, exist_ok=True)

            self.store.update_job(
                jid,
                title=meta["title"],
                author=meta["author"],
                author_id=meta.get("author_id") or None,
                aweme_id=meta["aweme_id"],
                out_dir=str(out_dir),
                quality=meta.get("quality") or None,
            )

            if meta.get("need_merge"):
                try:
                    self._run_merge(jid, job, meta, out_dir, cfg)
                    return
                except (Cancelled, Paused):
                    # 暂停/取消不是「拉不动」，别触发降级
                    raise
                except Exception as exc:  # noqa: BLE001
                    # DASH 直链拉不动（CDN 403 / 分片损坏）：退回整段 MP4，
                    # 宁可低一档画质也要把文件交到用户手里
                    self.store.update_job(
                        jid, stage="退回整段 MP4", error=f"DASH 失败，已降级：{exc}"
                    )
                    self.store.clear_assets(jid)
                    meta = self._extract(job, cfg, fallback=True)
                    # 降级后实际档位变了，标签必须跟着改，否则会出现
                    # 「任务写 480P、文件其实是 720P」
                    self.store.update_job(jid, quality=meta.get("quality") or None)

            self.store.add_assets(jid, meta["assets"])
            file_template = cfg.get("file_template") or DEFAULT_FILE_TEMPLATE
            assets = meta["assets"]
            # 重试时沿用上一轮已经确定的名字，不要每跑一次就多一个「(2)」
            reuse = {
                Path(a["path"]) for a in self.store.get_assets(jid) if a.get("path")
            }
            total_bytes = 0
            for asset in assets:
                self._check(jid)
                stem = build_file_stem(
                    meta, platform, file_template,
                    # 实况图「图+视频」成对时两者用同一个序号
                    index=int(asset.get("index") or (asset["idx"] + 1)),
                    total=len(assets),
                )
                ext = guess_ext(asset["url"], asset["kind"])
                dest = unique_path(out_dir / f"{stem}{ext}", keep=reuse)
                size = self._download_asset(jid, platform, asset, dest, cfg,
                                            total_bytes)
                total_bytes += size
                self.store.update_asset(
                    jid, asset["idx"], path=str(dest), size=size, status="done"
                )
                done = self.store.get_job(jid)["done_assets"] + 1
                self.store.update_job(
                    jid, done_assets=done, bytes_done=total_bytes
                )

            self._check(jid)
            self.store.update_job(jid, status="done", bytes_total=total_bytes,
                                  stage=None, error=None)
        except Paused:
            # 保留 .part，点「继续」时从断点接着下
            self.store.update_job(jid, status="paused", stage=None, error=None)
            with self._lock:
                self._cancelled.discard(jid)
        except Cancelled:
            self.store.update_job(jid, status="cancelled", error="已取消", stage=None)
            freed = self._discard_downloads(jid, job)
            if freed:
                self.store.update_job(jid, bytes_done=0)
            with self._lock:
                self._cancelled.discard(jid)
        except PlatformError as exc:
            # 抖音 ExtractError、B 站 BiliError 都是它的子类，这里只认一个类型
            self.store.update_job(jid, status="failed", error=str(exc), stage=None)
            with self._lock:
                self._cancelled.discard(jid)
        except Exception as exc:  # noqa: BLE001 - 兜底，避免线程静默死掉
            self.store.update_job(jid, status="failed",
                                  error=f"{type(exc).__name__}: {exc}", stage=None)
            with self._lock:
                self._cancelled.discard(jid)

    # ---------- DASH：下载两路再合成 ----------

    def _run_merge(self, jid: str, job: dict, meta: dict, out_dir: Path,
                   cfg: dict) -> None:
        """B 站 DASH：视频流 + 音频流分别落盘，再用 mp4mux 合成一个 MP4。

        DASH 直链拉不动时（部分 CDN 会 403）退回 `fnval=1` 的整段 MP4，
        宁可低一档也要让用户拿到文件。
        """
        assets = meta.get("assets") or []
        platform_name = meta.get("platform") or job.get("platform") or ""
        video = next((a for a in assets if a.get("role") == "video"), None)
        audio = next((a for a in assets if a.get("role") == "audio"), None)
        if video is None:
            raise RuntimeError("DASH 任务缺少视频流")

        reuse = {
            Path(a["path"]) for a in self.store.get_assets(jid) if a.get("path")
        }
        stem = build_file_stem(
            meta, platform_name, cfg.get("file_template") or DEFAULT_FILE_TEMPLATE,
        )
        dest = unique_path(out_dir / f"{stem}.mp4", keep=reuse)
        tmp_video = dest.with_suffix(".mp4.vpart")
        tmp_audio = dest.with_suffix(".mp4.apart")
        self._register_partials(jid, tmp_video, tmp_audio, dest)

        try:
            self.store.add_assets(jid, [video])

            # 先问一下两路各多大，进度条才能按真实字节走
            headers = self._headers_for(platform_name, video, cfg)
            opener = self._opener(cfg)
            total = self._remote_size(video, headers, opener)
            if audio is not None:
                total += self._remote_size(audio, headers, opener)
            if total:
                self.store.update_job(jid, bytes_total=total)

            self.store.update_job(jid, stage="下载视频流")
            self._fetch_to(jid, platform_name, video, tmp_video, cfg, base_bytes=0)

            audio_size = 0
            video_size = tmp_video.stat().st_size
            if audio is not None:
                self.store.update_job(jid, stage="下载音频流")
                audio_size = self._fetch_to(jid, platform_name, audio, tmp_audio, cfg,
                                            base_bytes=video_size)
            self.store.update_job(jid, bytes_done=video_size + audio_size)

            self._check(jid)

            self.store.update_job(jid, stage="合成 MP4")
            try:
                stats = mp4mux.mux(
                    tmp_video,
                    tmp_audio if (audio is not None and tmp_audio.exists()) else None,
                    dest,
                    progress=self._merge_progress(jid),
                )
                del stats
            except mp4mux.MuxError as exc:
                raise RuntimeError(f"音视频合成失败：{exc}") from exc

            self.store.update_asset(
                jid, video["idx"], path=str(dest), status="done",
                size=dest.stat().st_size,
            )
            self.store.update_job(
                jid,
                status="done",
                done_assets=1,
                bytes_total=dest.stat().st_size,
                bytes_done=dest.stat().st_size,
                stage=None,
            )
            self._forget_partials(jid)
        except (Cancelled, Paused):
            raise
        finally:
            # 暂停时保留两路分片，继续下载就不用重头再来
            if not self._is_paused(jid):
                tmp_video.unlink(missing_ok=True)
                tmp_audio.unlink(missing_ok=True)

    def _merge_progress(self, jid: str):
        """合成阶段的进度回调：把百分比写进 stage（不单独刷字节数）。"""
        state = {"last": 0.0}

        def report(fraction: float) -> None:
            self._check(jid)
            now = time.monotonic()
            if now - state["last"] < 0.5 and fraction < 1.0:
                return
            state["last"] = now
            self.store.update_job(jid, stage=f"合成 MP4 {fraction * 100:.0f}%")

        return report

    # ---------- 下载 ----------

    def _headers_for(self, platform_name: str, asset: dict, cfg: dict) -> dict:
        """下载用的请求头由平台提供（Referer / Cookie 各平台不同）。"""
        platform = platforms.find(platform_name) or platforms.resolve(
            asset.get("referer") or ""
        )
        headers = platform.media_headers(cfg)
        # 资产自带请求头（TikTok 的 Cookie/Referer）优先
        for key, value in (asset.get("headers") or {}).items():
            headers[str(key)] = str(value)
        # 资产自己带 Referer 时以它为准（例如 B 站 DASH 分片固定 bilibili 来源）
        if asset.get("referer"):
            headers["Referer"] = asset["referer"]
        return headers

    def _opener(self, cfg: dict):
        handlers = []
        if cfg.get("proxy"):
            handlers.append(
                urllib.request.ProxyHandler(
                    {"http": cfg["proxy"], "https": cfg["proxy"]}
                )
            )
        return urllib.request.build_opener(*handlers)

    def _remote_size(self, asset: dict, headers: dict, opener) -> int:
        """用一次 Range 探测拿总长度，失败就返回 0（不影响下载）。"""
        try:
            req = urllib.request.Request(
                asset["url"], headers={**headers, "Range": "bytes=0-0"}
            )
            with opener.open(req, timeout=20) as resp:
                raw = resp.headers.get("Content-Range") or ""
                if "/" in raw:
                    return int(raw.rsplit("/", 1)[-1])
                return int(resp.headers.get("Content-Length") or 0)
        except Exception:  # noqa: BLE001
            return 0

    def _fetch_to(self, jid: str, platform_name: str, asset: dict, dest: Path,
                  cfg: dict, base_bytes: int = 0) -> int:
        """把一路媒体流写到 dest，支持断点续传与多 CDN 回退。"""
        urls = [u for u in (asset.get("urls") or [asset.get("url")]) if u]
        if not urls:
            raise RuntimeError("没有可用的下载地址")
        headers = self._headers_for(platform_name, asset, cfg)
        opener = self._opener(cfg)
        retries = max(1, int(cfg.get("retries", 3)))

        last: Exception | None = None
        for attempt in range(retries):
            url = urls[attempt % len(urls)] if len(urls) > 1 else urls[0]
            try:
                self._stream_to_file(jid, url, headers, opener, dest, base_bytes)
                return dest.stat().st_size
            except (Cancelled, Paused):
                # 暂停/取消不重试，也不清理 .part（恢复时接着下）
                raise
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(1.0 * (attempt + 1))
        raise RuntimeError(f"下载失败：{last}")

    def _stream_to_file(self, jid: str, url: str, headers: dict, opener,
                        dest: Path, base_bytes: int) -> None:
        offset = dest.stat().st_size if dest.exists() else 0
        req_headers = dict(headers)
        if offset:
            req_headers["Range"] = f"bytes={offset}-"
        req = urllib.request.Request(url, headers=req_headers)
        written = offset
        last_update = 0.0
        with opener.open(req, timeout=60) as resp:
            # 服务器不认 Range 就从头写，避免拼出半个文件
            if offset and resp.status != 206:
                offset = written = 0
            mode = "ab" if offset else "wb"
            with open(dest, mode) as fh:
                while True:
                    self._check(jid)
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    written += len(chunk)
                    now = time.monotonic()
                    if now - last_update >= 0.5:
                        last_update = now
                        self.store.update_job(jid, bytes_done=base_bytes + written)

    def _download_asset(self, jid: str, platform_name: str, asset: dict, dest: Path,
                        cfg: dict, base_bytes: int = 0) -> int:
        # 不要因为「目标文件已存在」就跳过：换过画质设置后旧文件会残留，
        # 直接跳过会得到「任务显示 720P、文件其实是 480P」。断点续传靠 .part。
        parts = asset.get("parts") or [asset["url"]]
        if len(parts) > 1:
            tmp = dest.with_suffix(dest.suffix + ".part")
            self._register_partials(jid, tmp, dest)
            headers = self._headers_for(platform_name, asset, cfg)
            opener = self._opener(cfg)
            last: Exception | None = None
            for attempt in range(max(1, cfg.get("retries", 3))):
                try:
                    with open(tmp, "wb") as fh:
                        for part in parts:
                            req = urllib.request.Request(part, headers=headers)
                            with opener.open(req, timeout=60) as resp:
                                while True:
                                    self._check(jid)
                                    chunk = resp.read(256 * 1024)
                                    if not chunk:
                                        break
                                    fh.write(chunk)
                    os.replace(tmp, dest)
                    self._forget_partials(jid)
                    return dest.stat().st_size
                except (Cancelled, Paused):
                    # 多段 durl 不支持断点续传，两种中断都从头再来
                    tmp.unlink(missing_ok=True)
                    raise
                except Exception as exc:  # noqa: BLE001
                    last = exc
                    tmp.unlink(missing_ok=True)
                    time.sleep(1.0 * (attempt + 1))
            raise RuntimeError(f"下载失败：{last}")

        single = {**asset, "urls": [asset["url"]]}
        tmp = dest.with_suffix(dest.suffix + ".part")
        self._register_partials(jid, tmp, dest)
        # 探一下总长度，进度条才能按真实字节动（失败就当不知道）
        if not self.store.get_job(jid).get("bytes_total"):
            probe = self._remote_size(
                single, self._headers_for(platform_name, asset, cfg),
                self._opener(cfg),
            )
            if probe:
                self.store.update_job(jid, bytes_total=probe)
        size = self._fetch_to(jid, platform_name, single, tmp, cfg, base_bytes)
        os.replace(tmp, dest)
        self._forget_partials(jid)
        return size


def free_space_gb(path: Path) -> float:
    try:
        return round(shutil.disk_usage(path).free / (1024 ** 3), 1)
    except OSError:
        return 0.0

