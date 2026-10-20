"""命令行入口：批量下载，不启动网页。"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import config as config_mod
from .engine import Engine, extract_urls
from .store import Store

_STATUS = {
    "queued": "排队",
    "running": "下载中",
    "done": "完成",
    "failed": "失败",
    "cancelled": "已取消",
    "skipped": "跳过",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="huzhmi", description="抖音无水印视频/图集下载（命令行）"
    )
    parser.add_argument("target", nargs="+", help="链接、分享文案，或含链接的 .txt 文件")
    parser.add_argument("-o", "--output", help="输出目录（覆盖配置）")
    parser.add_argument("-c", "--cookie", help="抖音 Cookie（覆盖配置）")
    parser.add_argument("--timeout", type=int, default=1800, help="等待秒数，默认 1800")
    args = parser.parse_args(argv)

    targets: list[str] = []
    for item in args.target:
        path = Path(item)
        if path.is_file():
            targets.append(path.read_text("utf-8", "ignore"))
        else:
            targets.append(item)
    text = "\n".join(targets)
    if not extract_urls(text):
        print("没找到链接。", file=sys.stderr)
        return 2

    patch: dict = {}
    if args.output:
        patch["output_dir"] = str(Path(args.output).expanduser().resolve())
    if args.cookie:
        patch["cookie"] = args.cookie
    cfg = config_mod.save(patch) if patch else config_mod.load()
    Path(cfg["output_dir"]).mkdir(parents=True, exist_ok=True)

    store = Store(str(config_mod.DB_PATH))
    engine = Engine(store, cfg)
    results = engine.submit(text)
    job_ids = [r["job_id"] for r in results if r.get("job_id")]
    for r in results:
        if r["status"] == "skipped":
            print(f"[跳过] {r['url']} —— {r['reason']}")
    if not job_ids:
        print("没有新任务。")
        return 0

    deadline = time.time() + args.timeout
    seen: dict[str, str] = {}
    try:
        while time.time() < deadline:
            pending = 0
            for jid in job_ids:
                job = store.get_job(jid)
                if not job:
                    continue
                status = job["status"]
                key = f"{status}:{job['done_assets']}/{job['total_assets']}"
                if seen.get(jid) != key:
                    seen[jid] = key
                    label = _STATUS.get(status, status)
                    title = job["title"] or job["url"]
                    print(f"[{label}] {title}  {job['done_assets']}/{job['total_assets']}")
                    if status == "failed" and job["error"]:
                        print(f"        原因：{job['error']}")
                if status in ("queued", "running"):
                    pending += 1
            if pending == 0:
                break
            time.sleep(0.8)
        else:
            print("等待超时，任务仍在后台数据库里。", file=sys.stderr)
            return 1
    except KeyboardInterrupt:
        print("\n已中断（当前任务会在后台继续完成）。")
        return 130
    finally:
        engine.shutdown()

    failed = [j for j in job_ids
              if (store.get_job(j) or {}).get("status") == "failed"]
    done = [j for j in job_ids if (store.get_job(j) or {}).get("status") == "done"]
    print(f"\n完成 {len(done)}，失败 {len(failed)}。")
    for jid in failed:
        job = store.get_job(jid)
        if job:
            print(f"  失败：{job['title'] or job['url']} —— {job['error']}")
    store.close()
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
