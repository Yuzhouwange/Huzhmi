"""音乐库：读标签 → 按专辑分组 → 规划「转换后的目标路径」。

「读取专辑并批量处理」的核心在这里：专辑名优先用标签，
没有标签就退回父目录名，这样杂乱目录也能整理成
`艺术家/专辑/01 标题.mp3`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import tags as tags_mod
from . import verify

_AUDIO_SUFFIXES = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".ape",
                   ".wma")
_UNKNOWN_ARTIST = "未知艺术家"
_UNKNOWN_ALBUM = "未分类"


@dataclass
class Track:
    source: Path
    title: str
    artist: str
    album: str
    track: int
    size: int = 0
    duration: float = 0.0
    ok: bool = True
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "source": str(self.source),
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "track": self.track,
            "size": self.size,
            "duration": self.duration,
            "ok": self.ok,
            "reason": self.reason,
        }


@dataclass
class Album:
    artist: str
    album: str
    tracks: list[Track] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "artist": self.artist,
            "album": self.album,
            "count": len(self.tracks),
            "tracks": [t.as_dict() for t in self.tracks],
        }


def _sanitize(name: str) -> str:
    from ..engine import safe_dirname  # 复用同一套清洗规则

    return safe_dirname(name, limit=80)


def scan_tracks(root: str | Path) -> list[Track]:
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"目录不存在：{root}")
    tracks: list[Track] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _AUDIO_SUFFIXES:
            continue
        info = verify.check(path)
        meta = tags_mod.read(path)
        artist = (meta.artist or "").strip() or _UNKNOWN_ARTIST
        album = (meta.album or "").strip() or path.parent.name or _UNKNOWN_ALBUM
        tracks.append(
            Track(
                source=path,
                title=(meta.title or path.stem).strip(),
                artist=artist,
                album=album,
                track=int(meta.track or 0),
                size=info.size,
                duration=info.duration,
                ok=info.ok,
                reason=info.reason,
            )
        )
    return tracks


def group_albums(tracks: list[Track]) -> list[Album]:
    albums: dict[tuple[str, str], Album] = {}
    for track in tracks:
        key = (track.artist, track.album)
        albums.setdefault(key, Album(track.artist, track.album)).tracks.append(track)
    for album in albums.values():
        album.tracks.sort(key=lambda t: (t.track or 10 ** 6, t.title.lower()))
    return sorted(albums.values(), key=lambda a: (a.artist, a.album))


def target_path(out_root: Path, track: Track, extension: str,
                index: int = 0) -> Path:
    """`输出目录/艺术家/专辑/01 标题.mp3`。"""
    number = track.track or index or 1
    stem = f"{number:02d} {track.title}" if number else track.title
    return Path(out_root) / _sanitize(track.artist) / _sanitize(track.album) / \
        f"{_sanitize(stem)}.{extension.lstrip('.')}"


def plan(root: str | Path, out_root: str | Path, extension: str = "mp3") -> dict:
    """扫目录 → 专辑清单 + 每首的目标路径。"""
    tracks = scan_tracks(root)
    albums = group_albums(tracks)
    out = Path(out_root)
    items: list[dict] = []
    for album in albums:
        for index, track in enumerate(album.tracks, start=1):
            target = target_path(out, track, extension, index)
            if track.source.suffix.lower() == f".{extension}":
                # 已经是目标格式：同路径就不用转了
                target = track.source
            items.append(
                {
                    "source": str(track.source),
                    "target": str(target),
                    "title": track.title,
                    "artist": track.artist,
                    "album": track.album,
                    "track": track.track or index,
                    "ok": track.ok,
                    "reason": track.reason,
                    "needs_convert": track.ok and target != track.source,
                }
            )
    return {
        "root": str(root),
        "out_root": str(out_root),
        "extension": extension,
        "total": len(items),
        "convertible": sum(1 for item in items if item["needs_convert"]),
        "broken": sum(1 for item in items if not item["ok"]),
        "albums": [album.as_dict() for album in albums],
        "items": items,
    }


__all__ = [
    "Album",
    "Track",
    "group_albums",
    "plan",
    "scan_tracks",
    "target_path",
]
