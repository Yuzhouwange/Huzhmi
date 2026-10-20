"""暂停 / 继续 / 暂停整个队列的单元测试（不联网，用假的下载器）。"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from huzhmi.engine import Cancelled, Engine, Paused
from huzhmi.store import Store

URL = "https://www.douyin.com/video/7304228124479262006"


class PauseTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = Store(str(self.root / "t.db"))
        self.cfg = {
            "output_dir": str(self.root),
            "concurrency": 1,
            "retries": 1,
            "folder_template": "{platform}/{author}",
            "file_template": "{title}",
        }
        self.engine = Engine(self.store, self.cfg)
        self.engine._extract = lambda job, cfg, fallback=False: {
            "aweme_id": "7304228124479262006",
            "title": "测试视频",
            "author": "测试作者",
            "kind": "video",
            "assets": [{"idx": 0, "kind": "video", "url": "https://x/y.mp4"}],
            "platform": "douyin",
            "created": 0,
        }
        self.calls = 0
        self.engine._download_asset = self._fake_download

    def tearDown(self) -> None:
        self.engine.shutdown()
        self.store.close()
        self._tmp.cleanup()

    def _fake_download(self, jid, platform_name, asset, dest, cfg, base_bytes=0):
        """第一次调用慢慢来（好让测试中途暂停），之后立刻完成。"""
        self.calls += 1
        if self.calls == 1:
            for _ in range(300):
                self.engine._check(jid)
                time.sleep(0.01)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x" * 32)
        return 32

    def _submit(self) -> str:
        results = self.engine.submit(URL)
        return results[0]["job_id"]

    def _wait_status(self, jid: str, *states: str, timeout: float = 20.0) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.store.get_job(jid)
            if job and job["status"] in states:
                return job["status"]
            time.sleep(0.03)
        job = self.store.get_job(jid)
        raise AssertionError(f"等待 {states} 超时，当前是 {job and job['status']}")

    # ---------- 单条任务 ----------

    def test_pause_then_resume(self) -> None:
        jid = self._submit()
        self._wait_status(jid, "running")
        self.assertTrue(self.engine.pause(jid))
        self.assertEqual(self._wait_status(jid, "paused"), "paused")

        job = self.store.get_job(jid)
        self.assertIsNone(job["error"])

        self.assertTrue(self.engine.resume(jid))
        self.assertEqual(self._wait_status(jid, "done"), "done")
        self.assertEqual(self.calls, 2, "恢复应该重新走一次下载")
        self.assertTrue((self.root / "抖音" / "测试作者" / "测试视频.mp4").is_file())

    def test_pause_keeps_partial_file(self) -> None:
        """真实下载器会把半截留在 .part；这里只验证暂停不会删目标文件目录。"""
        jid = self._submit()
        self._wait_status(jid, "running")
        self.engine.pause(jid)
        self._wait_status(jid, "paused")
        # 暂停中的任务不该有成品文件
        self.assertFalse((self.root / "抖音" / "测试作者" / "测试视频.mp4").exists())

    def test_cannot_pause_queued_job(self) -> None:
        other = Engine(self.store, self.cfg)
        try:
            other.pause_queue(True)
            jid = other.submit(URL)[0]["job_id"]
            time.sleep(0.3)
            self.assertFalse(other.pause(jid), "还没开始下载的任务不能暂停")
            other.pause_queue(False)
        finally:
            other.shutdown()

    def test_resume_only_paused(self) -> None:
        jid = self._submit()
        self._wait_status(jid, "done")
        self.assertFalse(self.engine.resume(jid), "完成的任务没有「继续」")

    def test_cancel_from_paused(self) -> None:
        jid = self._submit()
        self._wait_status(jid, "running")
        self.engine.pause(jid)
        self._wait_status(jid, "paused")
        self.assertTrue(self.engine.cancel(jid))
        self.assertEqual(self._wait_status(jid, "cancelled"), "cancelled")

    # ---------- 整个队列 ----------

    def test_queue_pause_blocks_new_jobs(self) -> None:
        self.assertTrue(self.engine.pause_queue(True))
        self.assertTrue(self.engine.queue_paused)
        jid = self._submit()
        time.sleep(0.5)
        self.assertEqual(self.store.get_job(jid)["status"], "queued",
                         "队列暂停时新任务不该开始下载")

        self.engine.pause_queue(False)
        self.assertFalse(self.engine.queue_paused)
        self.assertEqual(self._wait_status(jid, "done"), "done")

    def test_queue_pause_also_stops_running(self) -> None:
        jid = self._submit()
        self._wait_status(jid, "running")
        self.engine.pause_queue(True)
        self.assertEqual(self._wait_status(jid, "paused"), "paused")

        self.engine.pause_queue(False)
        self.assertEqual(self._wait_status(jid, "done"), "done")

    def test_queue_pause_does_not_resume_manual_pause(self) -> None:
        """手动暂停的任务，恢复队列时不该被自动拉起来。"""
        jid = self._submit()
        self._wait_status(jid, "running")
        self.engine.pause(jid)
        self._wait_status(jid, "paused")

        self.engine.pause_queue(True)
        self.engine.pause_queue(False)
        time.sleep(0.6)
        self.assertEqual(self.store.get_job(jid)["status"], "paused",
                         "手动暂停的任务应该保持暂停")


class PauseSignalTest(unittest.TestCase):
    """_check 的语义：暂停抛 Paused，取消抛 Cancelled，互不干扰。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self._tmp.name) / "t.db"))
        self.engine = Engine(self.store, {"output_dir": self._tmp.name,
                                          "concurrency": 1})

    def tearDown(self) -> None:
        self.engine.shutdown()
        self.store.close()
        self._tmp.cleanup()

    def test_check_raises_paused(self) -> None:
        with self.engine._lock:
            self.engine._paused.add("j1")
        with self.assertRaises(Paused):
            self.engine._check("j1")

    def test_check_raises_cancelled_first(self) -> None:
        with self.engine._lock:
            self.engine._paused.add("j2")
            self.engine._cancelled.add("j2")
        with self.assertRaises(Cancelled):
            self.engine._check("j2")

    def test_check_passes_when_clear(self) -> None:
        self.engine._check("j3")  # 不抛就是通过


if __name__ == "__main__":
    unittest.main()
