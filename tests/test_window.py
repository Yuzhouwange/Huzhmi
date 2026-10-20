"""窗口模式与一键暂停的单元测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from huzhmi import window


class WindowArgsTest(unittest.TestCase):
    def test_args_have_app_mode_and_own_profile(self) -> None:
        args = window._args("C:/msedge.exe", "http://127.0.0.1:8765/",
                            Path(r"C:\data\window"))
        joined = " ".join(args)
        self.assertIn("--app=http://127.0.0.1:8765/", joined)
        self.assertIn("--user-data-dir=", joined)
        self.assertIn("--window-size=", joined)
        self.assertIn("--no-first-run", joined)
        # 第一条必须还是浏览器本身
        self.assertEqual(args[0], "C:/msedge.exe")

    def test_open_window_without_browser_returns_none(self) -> None:
        original = window.available
        window.available = lambda: None
        self.addCleanup(setattr, window, "available", original)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(window.open_window("http://x/", Path(tmp)))

    def test_open_window_creates_profile_dir(self) -> None:
        calls = {}

        class FakePopen:
            def __init__(self, args, **kwargs):
                calls["args"] = args
                calls["kwargs"] = kwargs

            def wait(self):
                return 0

        original_available = window.available
        original_popen = window.subprocess.Popen
        window.available = lambda: "C:/msedge.exe"
        window.subprocess.Popen = FakePopen
        self.addCleanup(setattr, window, "available", original_available)
        self.addCleanup(setattr, window.subprocess, "Popen", original_popen)

        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "window"
            proc = window.open_window("http://127.0.0.1:1/", profile)
            self.assertIsNotNone(proc)
            self.assertTrue(profile.is_dir(), "profile 目录要提前建好")
            self.assertIn("--app=http://127.0.0.1:1/", " ".join(calls["args"]))


class WindowedBuildTest(unittest.TestCase):
    def test_build_script_defaults_to_windowed(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "build_exe.py").read_text(
            encoding="utf-8")
        self.assertIn('"--console" if console else "--windowed"', source)
        self.assertIn("--console", source)


if __name__ == "__main__":
    unittest.main()
