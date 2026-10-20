"""音乐体检 + 转换产物校验的测试。"""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from douyinkit import media


def wav_bytes(seconds: float = 2.0, rate: int = 44100, channels: int = 2) -> bytes:
    frames = int(rate * seconds)
    data = b"\x00" * (frames * channels * 2)
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * channels * 2,
                      channels * 2, 16)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", len(data)) + data)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def mp3_bytes(seconds: float = 2.0, bitrate: int = 128) -> bytes:
    """128kbps / 44.1kHz / 立体声 的帧头 + 足够长的假数据。"""
    head = bytes([0xFF, 0xFB, 0x90, 0x00])   # MPEG1 Layer3 128k 44.1k
    size = int(bitrate * 1000 / 8 * seconds)
    return head + b"\x00" * max(0, size - len(head))


class VerifyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_html_saved_as_mp3_is_caught(self) -> None:
        path = self._write("bad.mp3", b"<!DOCTYPE html>\n<html><body>403</body></html>")
        info = media.verify.check(path)
        self.assertFalse(info.ok)
        self.assertEqual(info.kind, "html")
        self.assertIn("网页", info.reason)

    def test_empty_file(self) -> None:
        path = self._write("empty.mp3", b"")
        info = media.verify.check(path)
        self.assertFalse(info.ok)
        self.assertEqual(info.kind, "empty")

    def test_valid_mp3(self) -> None:
        path = self._write("good.mp3", mp3_bytes(3.0))
        info = media.verify.check(path)
        self.assertTrue(info.ok, info.reason)
        self.assertEqual(info.kind, "mp3")
        self.assertAlmostEqual(info.duration, 3.0, delta=0.6)
        self.assertEqual(info.sample_rate, 44100)

    def test_mp3_suffix_without_header(self) -> None:
        path = self._write("fake.mp3", b"just some bytes" * 2000)
        info = media.verify.check(path)
        self.assertFalse(info.ok)
        self.assertIn("MP3 头", info.reason)

    def test_valid_wav(self) -> None:
        path = self._write("good.wav", wav_bytes(2.0))
        info = media.verify.check(path)
        self.assertTrue(info.ok, info.reason)
        self.assertEqual(info.kind, "wav")
        self.assertAlmostEqual(info.duration, 2.0, delta=0.05)
        self.assertEqual(info.channels, 2)

    def test_truncated_wav(self) -> None:
        data = bytearray(wav_bytes(2.0))
        path = self._write("cut.wav", bytes(data[: len(data) // 2]))
        info = media.verify.check(path)
        self.assertFalse(info.ok)
        self.assertIn("截断", info.reason)

    def test_tiny_audio_file(self) -> None:
        path = self._write("tiny.mp3", mp3_bytes(2.0)[:4000])
        info = media.verify.check(path)
        self.assertFalse(info.ok)
        self.assertIn("字节", info.reason)

    def test_scan_directory(self) -> None:
        self._write("a.mp3", mp3_bytes(3.0))
        self._write("b.wav", wav_bytes(2.0))
        self._write("c.mp3", b"<!DOCTYPE html><html></html>")
        result = media.verify.scan(self.root)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["broken"], 1)
        self.assertEqual(result["kinds"].get("html"), 1)

    def test_report_mentions_bad_file(self) -> None:
        self._write("bad.mp3", b"<!DOCTYPE html>")
        text = media.verify.report(media.verify.scan(self.root))
        self.assertIn("bad.mp3", text)
        self.assertIn("有问题", text)

    def test_scan_missing_dir(self) -> None:
        with self.assertRaises(FileNotFoundError):
            media.verify.scan(self.root / "nope")


class TransformVerificationTest(unittest.TestCase):
    """转换产出必须过体检，坏文件要判失败并删掉。"""

    def test_bad_output_fails_job(self) -> None:
        import json
        import time
        from pathlib import Path

        from douyinkit.engine import Engine
        from douyinkit.store import Store

        class FakeTransform:
            label = "假转换"
            kind = "audio"
            extension = "mp3"
            needs = None

            def run(self, src, dst, on_progress=None, cfg=None):
                # 模拟「平台错误页被存成音频」：写一段 HTML 进去
                Path(dst).write_bytes(b"<!DOCTYPE html><html>403</html>")
                return {"size": 32}

        original_get = media.get
        media.get = lambda name: FakeTransform()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(str(root / "t.db"))
            engine = Engine(store, {"output_dir": str(root), "concurrency": 1})
            try:
                src = root / "song.mp4"
                src.write_bytes(b"\x00" * 100)
                payload = json.dumps({"transform": {
                    "op": "audio_mp3", "src": str(src),
                    "dst": str(root / "song.mp3")}})
                jid = store.create_job("transform:audio_mp3/x", "k", payload,
                                       str(root))
                store.add_assets(jid, [{"idx": 0, "kind": "audio", "url": "x"}])
                store.add_step(jid, 0, "audio_mp3", {})

                engine._run(jid)

                job = store.get_job(jid)
                self.assertEqual(job["status"], "failed")
                self.assertIn("校验失败", job["error"])
                self.assertFalse((root / "song.mp3").exists(), "坏产物要删掉")
            finally:
                engine.shutdown()
                store.close()
                media.get = original_get


def json_payload(src: Path, root: Path) -> str:
    import json

    return json.dumps({"transform": {"op": "audio_m4a", "src": str(src),
                                     "dst": str(root / "song.m4a")}})


if __name__ == "__main__":
    unittest.main()
