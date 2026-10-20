"""本地作品档案：扫描下载目录里的视频 / 图片，供浏览、播放、删除。

只读扫描 + 单文件删除，路径限制在下载目录内。
"""

from __future__ import annotations

import os
from pathlib import Path

VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm", ".flv", ".m4v")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
AUDIO_SUFFIXES = (".mp3", ".wav", ".flac", ".m4a", ".ogg")
_ALL = VIDEO_SUFFIXES + IMAGE_SUFFIXES


def _allowed(root: Path, target: Path) -> bool:
    root = root.resolve()
    target = target.resolve()
    return target == root or root in target.parents


def scan(root: str | Path, query: str = "", limit: int = 3000,
         offset: int = 0) -> dict:
    base = Path(str(root or "")).expanduser()
    if not base.is_dir():
        raise FileNotFoundError(f"目录不存在：{base}")
    needle = str(query or "").strip().casefold()
    items: list[dict] = []
    for folder, dirs, files in os.walk(base):
        dirs[:] = [name for name in dirs if not name.startswith("_")]
        for name in files:
            path = Path(folder) / name
            if path.suffix.lower() not in _ALL:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            kind = "video" if path.suffix.lower() in VIDEO_SUFFIXES else "image"
            title = path.stem
            parent = path.parent.name
            if needle and needle not in f"{title} {parent} {name}".casefold():
                continue
            items.append({
                "path": str(path),
                "name": name,
                "title": title,
                "folder": str(path.parent),
                "parent": parent,
                "kind": kind,
                "ext": path.suffix.lower().lstrip("."),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
    items.sort(key=lambda row: row["mtime"], reverse=True)
    total = len(items)
    start = max(0, int(offset))
    size = max(1, int(limit))
    page = items[start:start + size]
    return {"root": str(base), "count": len(page), "total": total,
            "offset": start, "has_more": start + size < total, "items": page}


def delete(root: str | Path, path: str | Path) -> bool:
    base = Path(str(root or "")).expanduser()
    target = Path(str(path or "")).expanduser()
    if not _allowed(base, target):
        raise PermissionError("路径不在下载目录内")
    if target.suffix.lower() not in _ALL or not target.is_file():
        return False
    target.unlink()
    return True


__all__ = ["AUDIO_SUFFIXES", "IMAGE_SUFFIXES", "VIDEO_SUFFIXES", "delete", "scan"]
