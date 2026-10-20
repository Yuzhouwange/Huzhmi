"""打包态的路径判断（不真的打包，模拟 sys.frozen）。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from huzhmi import paths


class FrozenPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._saved = {
            "frozen": getattr(sys, "frozen", None),
            "_MEIPASS": getattr(sys, "_MEIPASS", None),
            "executable": sys.executable,
        }

    def tearDown(self) -> None:
        for attr, value in self._saved.items():
            if value is None:
                if hasattr(sys, attr):
                    delattr(sys, attr)
            else:
                setattr(sys, attr, value)
        self._tmp.cleanup()

    def _freeze(self) -> Path:
        bundle = self.tmp / "meipass"
        (bundle / "web").mkdir(parents=True)
        (bundle / "web" / "index.html").write_text("<html></html>", encoding="utf-8")
        exe_dir = self.tmp / "dist"
        exe_dir.mkdir()
        sys.frozen = True  # type: ignore[attr-defined]
        sys._MEIPASS = str(bundle)  # type: ignore[attr-defined]
        sys.executable = str(exe_dir / "Huzhmi.exe")
        return bundle

    def test_dev_mode_uses_repo(self) -> None:
        sys.frozen = False  # type: ignore[attr-defined]
        self.assertFalse(paths.is_frozen())
        self.assertEqual(paths.resource_dir(), Path(paths.__file__).resolve().parent.parent)
        self.assertEqual(paths.data_dir().name, "data")

    def test_frozen_resources_come_from_meipass(self) -> None:
        bundle = self._freeze()
        self.assertTrue(paths.is_frozen())
        self.assertEqual(paths.resource_dir(), bundle)
        self.assertTrue((paths.resource_dir() / "web" / "index.html").is_file())

    def test_frozen_data_sits_next_to_exe(self) -> None:
        self._freeze()
        data = paths.data_dir()
        self.assertEqual(data, self.tmp / "dist" / "data")
        self.assertTrue(data.is_dir())
        # 数据目录必须真的可写
        probe = data / "x.txt"
        probe.write_text("ok", encoding="utf-8")
        self.assertEqual(probe.read_text(encoding="utf-8"), "ok")

    def test_env_override_wins(self) -> None:
        self._freeze()
        custom = self.tmp / "custom-data"
        import os
        os.environ["HUZHMI_DATA_DIR"] = str(custom)
        try:
            self.assertEqual(paths.data_dir(), custom)
            self.assertTrue(custom.is_dir())
        finally:
            os.environ.pop("HUZHMI_DATA_DIR", None)


if __name__ == "__main__":
    unittest.main()
