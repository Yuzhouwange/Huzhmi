"""把「每条作品一个文件夹」的老库拍平。

老版本按 `{platform}/{author}/{title}/01.mp4` 落盘，于是作者目录下全是文件夹，
想连播还得一层层点进去。这里给出「探测 → 计划 → 执行」三步，先出计划再动手：

    root/
      标题 A/01.mp4        ->  root/标题 A.mp4
      标题 B/01.mp4        ->  root/标题 B.mp4
      标题 C/01.jpg,02.jpg ->  root/标题 C - 01.jpg, 标题 C - 02.jpg

文件名冲突时自动加 ` (2)`；搬完只删「已经空了」的子目录，不递归删除有内容的目录。
"""

from __future__ import annotations

import shutil
from pathlib import Path

MEDIA_EXT = {
    # 视频
    ".mp4", ".mkv", ".mov", ".m4v", ".webm", ".flv", ".avi", ".ts", ".wmv",
    # 音频
    ".mp3", ".m4a", ".aac", ".flac", ".wav",
    # 图集
    ".jpg", ".jpeg", ".png", ".webp", ".heic",
}

# 半边下载文件不能当成品搬走
_PARTIAL_SUFFIX = (".part", ".vpart", ".apart", ".download", ".crdownload")


def is_media(path: Path) -> bool:
    if any(path.name.lower().endswith(suffix) for suffix in _PARTIAL_SUFFIX):
        return False
    return path.suffix.lower() in MEDIA_EXT


def _free_name(target: Path, taken: set[Path]) -> Path:
    if target not in taken and not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for counter in range(2, 1000):
        candidate = target.with_name(f"{stem} ({counter}){suffix}")
        if candidate not in taken and not candidate.exists():
            return candidate
    raise OSError(f"同名文件太多：{target.name}")


def plan_flatten(root: Path) -> list[tuple[Path, Path]]:
    """列出 (源文件, 目标文件)。只处理 root 之下、夹在子目录里的媒体文件。"""
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)

    moves: list[tuple[Path, Path]] = []
    taken: set[Path] = set()
    for folder in sorted(p for p in root.rglob("*") if p.is_dir()):
        files = [p for p in sorted(folder.iterdir()) if p.is_file() and is_media(p)]
        if not files:
            continue
        for index, source in enumerate(files, start=1):
            suffix = f" - {index:02d}" if len(files) > 1 else ""
            target = root / f"{folder.name}{suffix}{source.suffix}"
            target = _free_name(target, taken)
            taken.add(target)
            moves.append((source, target))
    return moves


def apply_moves(moves: list[tuple[Path, Path]]) -> int:
    moved = 0
    for source, target in moves:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(target)
        shutil.move(str(source), str(target))
        moved += 1
    return moved


def prune_empty_dirs(root: Path) -> int:
    """删掉空目录（由深到浅），有内容的目录一律不动。"""
    removed = 0
    for folder in sorted((p for p in Path(root).rglob("*") if p.is_dir()),
                         key=lambda p: len(p.parts), reverse=True):
        try:
            next(folder.iterdir())
            continue
        except StopIteration:
            pass
        except OSError:
            continue
        try:
            folder.rmdir()
            removed += 1
        except OSError:
            continue
    return removed
