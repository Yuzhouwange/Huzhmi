"""本地音乐库：扫描音乐目录、读标签、找歌词/封面、导出 M3U。

给「本地音乐库」面板用。只读 + 删除单个文件，不碰转码（转码走 service）。
路径一律限制在音乐目录内，避免变成任意文件浏览器。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from ..media import tags, verify

AUDIO_SUFFIXES = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".ape",
                  ".wma", ".opus", ".aiff")
_CACHE: dict[str, tuple[float, int, dict]] = {}
_CACHE: dict[str, tuple[float, int, dict]] = {}
_CACHE_TTL = 30


def _allowed(root: Path, target: Path) -> bool:
    root = root.resolve()
    target = target.resolve()
    return target == root or root in target.parents


def scan(root: str | Path, query: str = "", limit: int = 5000,
         offset: int = 0) -> dict:
    base = Path(str(root or "")).expanduser()
    if not base.is_dir():
        raise FileNotFoundError(f"音乐目录不存在：{base}")
    items: list[dict] = []
    needle = str(query or "").strip().casefold()
    for folder, _dirs, files in os.walk(base):
        for name in files:
            path = Path(folder) / name
            if path.suffix.lower() not in AUDIO_SUFFIXES:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            key = str(path)
            cached = _CACHE.get(key)
            if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
                item = dict(cached[2])
            else:
                meta = tags.read(path)
                info = verify.check(path)
                item = {
                    "path": str(path),
                    "name": path.name,
                    "title": meta.title or path.stem,
                    "artist": meta.artist or "",
                    "album": meta.album or "",
                    "track": int(meta.track or 0),
                    "duration": round(info.duration, 2),
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "ext": path.suffix.lower().lstrip("."),
                    "ok": info.ok,
                    "has_lrc": path.with_suffix(".lrc").is_file(),
                    "has_cover": (path.parent / "cover.jpg").is_file(),
                }
                _CACHE[key] = (stat.st_mtime, stat.st_size, dict(item))
            if needle and needle not in (
                    f"{item['title']} {item['artist']} {item['album']} {item['name']}"
            ).casefold():
                continue
            items.append(item)
    items.sort(key=lambda row: (row["artist"].casefold(), row["album"].casefold(),
                                row["track"], row["title"].casefold()))
    total = len(items)
    start = max(0, int(offset))
    size = max(1, int(limit))
    page = items[start:start + size]
    return {"root": str(base), "count": len(page), "total": total,
            "offset": start, "has_more": start + size < total, "items": page}


def lyrics_text(path: str | Path) -> str:
    lrc = Path(str(path or "")).with_suffix(".lrc")
    if not lrc.is_file():
        return ""
    try:
        return lrc.read_text("utf-8-sig", errors="replace")
    except OSError:
        return ""


def lyrics_for(root: str | Path, path: str | Path) -> str:
    base = Path(str(root or "")).expanduser()
    target = Path(str(path or "")).expanduser()
    if not _allowed(base, target):
        raise PermissionError("路径不在音乐目录内")
    return lyrics_text(target)


def delete(root: str | Path, path: str | Path) -> bool:
    base = Path(str(root or "")).expanduser()
    target = Path(str(path or "")).expanduser()
    if not _allowed(base, target):
        raise PermissionError("路径不在音乐目录内")
    if target.suffix.lower() not in AUDIO_SUFFIXES or not target.is_file():
        return False
    target.unlink()
    target.with_suffix(".lrc").unlink(missing_ok=True)
    _CACHE.pop(str(target), None)
    return True


def export_m3u(root: str | Path, paths: list[str] | None = None) -> str:
    base = Path(str(root or "")).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    if paths:
        files = []
        for raw in paths:
            path = Path(str(raw or ""))
            if _allowed(base, path) and path.is_file():
                files.append(path)
    else:
        files = [item["path"] for item in scan(base, limit=5000)["items"]]
    lines = ["#EXTM3U"]
    for path in files:
        meta = tags.read(path)
        duration = int(verify.check(path).duration or 0)
        label = f"{meta.artist} - {meta.title}" if meta.artist else meta.title
        lines.append(f"#EXTINF:{duration},{label}")
        lines.append(str(path))
    target = base / f"音乐库-{time.strftime('%Y%m%d-%H%M%S')}.m3u8"
    target.write_text("\n".join(lines), encoding="utf-8")
    return str(target)


__all__ = ["AUDIO_SUFFIXES", "delete", "export_m3u", "lyrics_for", "lyrics_text",
           "scan"]
