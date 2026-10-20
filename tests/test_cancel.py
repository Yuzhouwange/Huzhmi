"""取消任务：任意状态可取消，正在下载的要连带删掉已下载部分。"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from douyinkit.engine import Engine
from douyinkit.store import Store

URL = "https://www.douyin.com/video/7304228124479262006"


class CancelTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.store = Store(str(self.root / "t.db"))
        self.cfg = {"output_dir": str(self.root), "concurrency": 1, "retries": 1,
                    "folder_template": "{platform}/{author}",
                    "file_template": "{title}"}
        self.engine = Engine(self.store, self.cfg)
        def fake_extract(job, cfg, fallback=False):
            ident = str(job.get("url") or "").rstrip("/").rsplit("/", 1)[-1]
            return {
                "aweme_id": ident,
                "title": f"测试{ident}",
                "author": "测试作者",
                "kind": "video",
                "assets": [{"idx": 0, "kind": "video", "url": "https://x/y.mp4"}],
                "platform": "douyin",
                "created": 0,
            }

        self.engine._extract = fake_extract
        self.calls = 0
        self.duration = 3.0          # 每次「下载」大概持续多久（秒）
        self.engine._download_asset = self._slow_download
        self._seq = 0

    def tearDown(self) -> None:
        self.engine.shutdown()
        time.sleep(0.3)          # 等下载线程把文件句柄放掉再删目录
        self.store.close()
        self._tmp.cleanup()

    def _slow_download(self, jid, platform_name, asset, dest, cfg, base_bytes=0):
        """写一点文件再慢慢往后写，好在中途取消。"""
        self.calls += 1
        tmp = dest.with_suffix(dest.suffix + ".part")
        self.engine._register_partials(jid, tmp, dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        loops = max(1, int(self.duration / 0.01))
        with open(tmp, "wb") as fh:
            for _ in range(loops):
                fh.write(b"x" * 4096)
                self.engine._check(jid)
                time.sleep(0.01)
        os.replace(tmp, dest)
        self.engine._forget_partials(jid)
        return dest.stat().st_size

    def _wait(self, jid, *states, timeout=20.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.store.get_job(jid)
            if job and job["status"] in states:
                return job
            time.sleep(0.03)
        job = self.store.get_job(jid)
        raise AssertionError(f"等待 {states} 超时，当前是 {job and job['status']}")

    def _submit(self) -> str:
        # 每个任务用不同的作品 ID：引擎现在会跳过「已在队列/下载中」的重复链接
        self._seq += 1
        url = f"https://www.douyin.com/video/{7304228124479262000 + self._seq}"
        return self.engine.submit(url)[0]["job_id"]

    def _partials(self, jid) -> list[Path]:
        return [p for p in self.root.rglob("*") if p.suffix in (".part", ".vpart",
                                                                ".apart")]

    # ---------- 单条 ----------

    def test_cancel_running_deletes_partial(self) -> None:
        jid = self._submit()
        self._wait(jid, "running")
        deadline = time.time() + 15
        while time.time() < deadline and not self._partials(jid):
            time.sleep(0.05)
        self.assertTrue(self._partials(jid), "取消前应该已经有半截文件")

        self.assertTrue(self.engine.cancel(jid))
        job = self._wait(jid, "cancelled")
        self.assertEqual(job["error"], "已取消")
        self.assertEqual(self._partials(jid), [], "取消后不能留下分片")
        self.assertEqual(list(self.root.rglob("*.mp4")), [], "不能留下成品")
        self.assertEqual(self.store.get_job(jid)["bytes_done"], 0)

    def test_cancel_queued_never_downloads(self) -> None:
        self.engine.pause_queue(True)          # 让任务停在排队
        jid = self._submit()
        time.sleep(0.2)
        self.assertEqual(self.store.get_job(jid)["status"], "queued")

        self.assertTrue(self.engine.cancel(jid))
        self.assertEqual(self.store.get_job(jid)["status"], "cancelled")

        self.engine.pause_queue(False)         # 恢复队列：它不该再跑
        time.sleep(0.6)
        self.assertEqual(self.calls, 0, "已取消的排队任务不能开始下载")
        self.assertEqual(self.store.get_job(jid)["status"], "cancelled")

    def test_cancel_paused_deletes_partial(self) -> None:
        jid = self._submit()
        self._wait(jid, "running")
        deadline = time.time() + 15
        while time.time() < deadline and not self._partials(jid):
            time.sleep(0.05)
        self.engine.pause(jid)
        self._wait(jid, "paused")
        self.assertTrue(self._partials(jid), "暂停时应保留分片")

        self.assertTrue(self.engine.cancel(jid))
        self._wait(jid, "cancelled")
        self.assertEqual(self._partials(jid), [], "取消暂停任务也要清掉分片")

    def test_cannot_cancel_finished(self) -> None:
        jid = self._submit()
        self._wait(jid, "done", timeout=20)
        self.assertFalse(self.engine.cancel(jid), "完成的任务不该能取消")

    # ---------- 一键 ----------

    def test_cancel_all_covers_queued_running_paused(self) -> None:
        self.duration = 120.0          # 测试期间谁都别下完
        # 单并发先摆出三种状态：先暂停 A，空出的线程给 B，C 自然排队
        paused_job = self._submit()
        self._wait(paused_job, "running")
        self.engine.pause(paused_job)
        self._wait(paused_job, "paused")

        running = self._submit()
        self._wait(running, "running")

        queued = self._submit()
        time.sleep(0.3)
        self.assertEqual(self.store.get_job(queued)["status"], "queued")

        count = self.engine.cancel_all()
        self.assertEqual(count, 3)

        # 取消是异步收尾的：等三个任务都落成 cancelled
        deadline = time.time() + 10
        while time.time() < deadline:
            states = {self.store.get_job(j)["status"]
                      for j in (paused_job, running, queued)}
            if states == {"cancelled"}:
                break
            time.sleep(0.05)
        for jid in (paused_job, running, queued):
            self.assertEqual(self.store.get_job(jid)["status"], "cancelled")
        self.assertEqual(self._partials("any"), [], "一键取消也要清掉分片")

    def test_cancel_all_leaves_done_and_failed(self) -> None:
        done = self._submit()
        self._wait(done, "done", timeout=20)
        failed = self.store.create_job("https://x/dead", "k-dead", platform="douyin")
        self.store.update_job(failed, status="failed", error="坏了")

        self.engine.cancel_all()
        self.assertEqual(self.store.get_job(done)["status"], "done")
        self.assertEqual(self.store.get_job(failed)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
