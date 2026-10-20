"""重启恢复：上次没跑完的任务要回到队列，而不是标成失败。"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from douyinkit.engine import Engine
from douyinkit.store import Store


class RecoverTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = Store(str(Path(self._tmp.name) / "t.db"))

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _job(self, key: str) -> str:
        return self.store.create_job(f"https://x/{key}", key, platform="bilibili")

    def test_interrupted_jobs_go_back_to_queue(self) -> None:
        queued = self._job("a")
        running = self._job("b")
        done = self._job("c")
        self.store.update_job(queued, status="queued")
        self.store.update_job(running, status="running", stage="下载视频流")
        self.store.update_job(done, status="done")

        ids = self.store.recover_interrupted()

        self.assertEqual(set(ids), {queued, running})
        for jid in (queued, running):
            job = self.store.get_job(jid)
            self.assertEqual(job["status"], "queued")
            self.assertIsNone(job["stage"], "恢复时要清掉上次的阶段文字")
        self.assertEqual(self.store.get_job(done)["status"], "done")

    def test_nothing_to_recover(self) -> None:
        self.assertEqual(self.store.recover_interrupted(), [])

    def test_engine_requeue_puts_them_in_line(self) -> None:
        jid = self._job("d")
        engine = Engine(self.store, {"output_dir": self._tmp.name, "concurrency": 1})
        engine.pause_queue(True)          # 只验证入队，不真的下载
        self.addCleanup(engine.pause_queue, False)
        self.addCleanup(engine.shutdown)
        self.assertEqual(engine.requeue([jid]), 1)
        time.sleep(0.2)
        self.assertEqual(self.store.get_job(jid)["status"], "queued")


if __name__ == "__main__":
    unittest.main()
