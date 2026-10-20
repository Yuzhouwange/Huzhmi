"""作品档案：扫描 / 筛选 / 删除。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from douyinkit import archive


class ArchiveTest(unittest.TestCase):
    def test_scan_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "作者").mkdir()
            (root / "作者" / "视频.mp4").write_bytes(b"x" * 1024)
            (root / "作者" / "图片.jpg").write_bytes(b"x" * 512)
            (root / "作者" / "说明.txt").write_text("ignore")
            data = archive.scan(root)
            self.assertEqual(data["count"], 2)
            kinds = {item["kind"] for item in data["items"]}
            self.assertEqual(kinds, {"video", "image"})
            filtered = archive.scan(root, "视频")
            self.assertEqual(filtered["count"], 1)
            target = Path(filtered["items"][0]["path"])
            self.assertTrue(archive.delete(root, target))
            self.assertFalse(target.exists())
            with self.assertRaises(PermissionError):
                archive.delete(root, root.parent / "outside.mp4")

    def test_scan_missing(self):
        with self.assertRaises(FileNotFoundError):
            archive.scan("D:/definitely-not-exists")
