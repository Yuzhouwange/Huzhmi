#!/usr/bin/env python3
"""把老库里的视频从「标题文件夹」里搬到上一层。

    python flatten_downloads.py "E:\\视频\\抖音\\可爱的光头强"           # 只看计划
    python flatten_downloads.py "E:\\视频\\抖音\\可爱的光头强" --apply   # 真的搬

搬完形如：

    E:\\视频\\抖音\\可爱的光头强\\开局一条狗.mp4
    E:\\视频\\抖音\\可爱的光头强\\大结局.mp4

只删除搬空的子目录；有内容的目录不碰。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from huzhmi.library import apply_moves, plan_flatten, prune_empty_dirs


def human(path: Path) -> str:
    return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(prog="flatten_downloads")
    parser.add_argument("root", help="要拍平的目录（作者目录）")
    parser.add_argument("--apply", action="store_true", help="真的移动文件")
    parser.add_argument("--keep-folders", action="store_true",
                        help="不要删除搬空的子目录")
    parser.add_argument("--limit", type=int, default=0,
                        help="只打印前 N 条计划，0 表示全部")
    args = parser.parse_args()

    root = Path(args.root).expanduser()
    if not root.is_dir():
        print(f"目录不存在：{root}")
        return 2

    moves = plan_flatten(root)
    if not moves:
        print(f"{root}\n  没有需要搬的文件（已经是平的，或者没有媒体文件）")
        return 0

    shown = moves if args.limit <= 0 else moves[: args.limit]
    for source, target in shown:
        print(f"  {source.relative_to(root)}")
        print(f"    -> {target.name}")
    if args.limit and len(moves) > args.limit:
        print(f"  … 还有 {len(moves) - args.limit} 条")

    folders = {source.parent for source, _ in moves}
    print(f"\n共 {len(moves)} 个文件，来自 {len(folders)} 个文件夹")

    if not args.apply:
        print("\n这是演练，没有改动任何文件。加 --apply 才会真的搬。")
        return 0

    moved = apply_moves(moves)
    removed = 0 if args.keep_folders else prune_empty_dirs(root)
    print(f"\n已移动 {moved} 个文件，清理空目录 {removed} 个")
    print(f"现在都在：{root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
