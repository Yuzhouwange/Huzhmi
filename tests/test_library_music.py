"""音乐库：标签读取、专辑分组、批量转换任务。"""

from __future__ import annotations

import struct
import tempfile
import time
import unittest
from pathlib import Path

from huzhmi import media
from huzhmi.media import library, tags


def id3v2(title: str, artist: str, album: str, track: str = "3") -> bytes:
    def frame(name: bytes, text: str) -> bytes:
        payload = b"\x03" + text.encode("utf-8")   # 编码 3 = UTF-8
        return name + struct.pack(">I", len(payload)) + b"\x00\x00" + payload

    body = (frame(b"TIT2", title) + frame(b"TPE1", artist)
            + frame(b"TALB", album) + frame(b"TRCK", track))
    size = len(body)
    sync = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F,
                  (size >> 7) & 0x7F, size & 0x7F])
    return b"ID3\x03\x00\x00" + sync + body


def mp3_with_tags(title: str, artist: str, album: str, track: str = "3") -> bytes:
    head = bytes([0xFF, 0xFB, 0x90, 0x00])
    return id3v2(title, artist, album, track) + head + b"\x00" * 200_000


def flac_with_tags(title: str, artist: str, album: str) -> bytes:
    comments = b"TITLE=" + title.encode() + b"\x00ARTIST=" + artist.encode() + \
               b"\x00ALBUM=" + album.encode()
    vendor = b"test"
    body = (struct.pack("<I", len(vendor)) + vendor
            + struct.pack("<I", 3) + b"".join(
                struct.pack("<I", len(part)) + part
                for part in comments.split(b"\x00")))
    block = bytes([0x80 | 4]) + len(body).to_bytes(3, "big") + body
    return b"fLaC" + block


def wav_with_id3v1(title: str = "Old Title") -> bytes:
    data = b"\x00" * (44100 * 2 * 2)   # 1 秒，避免被体检判成“截断”
    fmt = struct.pack("<HHIIHH", 1, 2, 44100, 44100 * 4, 4, 16)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", len(data)) + data)
    audio = b"RIFF" + struct.pack("<I", len(body)) + body
    # ID3v1 天生只能存 latin-1，中文标题只能走 ID3v2
    tag = (b"TAG" + title.encode("latin-1", "replace")[:30].ljust(30, b"\x00")
           + b"A" * 30 + b"B" * 30 + b"2024" + b"\x00" * 30 + b"\x00")
    return audio + tag


class TagTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_id3v2(self) -> None:
        path = self._write("a.mp3", mp3_with_tags("晴天", "周杰伦", "叶惠美", "3"))
        meta = tags.read(path)
        self.assertEqual((meta.title, meta.artist, meta.album), ("晴天", "周杰伦", "叶惠美"))
        self.assertEqual(meta.track, 3)

    def test_id3v1(self) -> None:
        path = self._write("b.wav", wav_with_id3v1("Old Title"))
        meta = tags.read(path)
        self.assertEqual(meta.title, "Old Title")

    def test_flac_comments(self) -> None:
        path = self._write("c.flac", flac_with_tags("曲名", "歌手", "专辑"))
        meta = tags.read(path)
        self.assertEqual((meta.title, meta.artist, meta.album), ("曲名", "歌手", "专辑"))

    def test_no_tags_falls_back_to_filename(self) -> None:
        path = self._write("没有标签的歌.mp3", mp3_with_tags("", "", ""))
        meta = tags.read(path)
        self.assertEqual(meta.title, "没有标签的歌")


class LibraryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.src = self.root / "in"
        self.src.mkdir()
        (self.src / "album1").mkdir()
        (self.src / "album1" / "01.mp3").write_bytes(
            mp3_with_tags("第一首", "歌手A", "专辑X", "1"))
        (self.src / "album1" / "02.mp3").write_bytes(
            mp3_with_tags("第二首", "歌手A", "专辑X", "2"))
        (self.src / "album1" / "03.wav").write_bytes(
            wav_with_id3v1("Track Three"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_group_by_album(self) -> None:
        tracks = library.scan_tracks(self.src)
        albums = library.group_albums(tracks)
        self.assertTrue(albums)
        names = {(a.artist, a.album) for a in albums}
        self.assertIn(("歌手A", "专辑X"), names)
        album = next(a for a in albums if a.album == "专辑X")
        self.assertEqual([t.title for t in album.tracks], ["第一首", "第二首"])

    def test_target_path_layout(self) -> None:
        track = library.Track(Path("x.mp3"), "晴天", "周杰伦", "叶惠美", 3)
        target = library.target_path(self.root / "out", track, "mp3")
        self.assertEqual(target.parts[-3:], ("周杰伦", "叶惠美", "03 晴天.mp3"))

    def test_plan_counts(self) -> None:
        result = library.plan(self.src, self.root / "out", "wav")
        self.assertEqual(result["total"], 3)
        self.assertTrue(result["albums"])
        self.assertEqual(result["extension"], "wav")

    def test_broken_file_marked(self) -> None:
        (self.src / "bad.mp3").write_bytes(b"<!DOCTYPE html><html></html>")
        result = library.plan(self.src, self.root / "out", "mp3")
        broken = [i for i in result["items"] if not i["ok"]]
        self.assertEqual(len(broken), 1)
        self.assertIn("网页", broken[0]["reason"])


class ConvertJobTest(unittest.TestCase):
    def test_submit_convert_creates_job(self) -> None:
        from huzhmi.engine import Engine
        from huzhmi.store import Store

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(str(root / "t.db"))
            engine = Engine(store, {"output_dir": str(root), "concurrency": 1})
            engine.pause_queue(True)      # 只验证建任务，不真的跑 ffmpeg
            try:
                src = root / "song.mp3"
                src.write_bytes(mp3_with_tags("歌", "人", "集"))
                dst = root / "out" / "人" / "集" / "01 歌.wav"
                jid = engine.submit_convert(src, dst, "audio_wav")
                self.assertIsNotNone(jid)
                job = store.get_job(jid)
                self.assertEqual(job["url"], f"convert:audio_wav/{src.name}")
                steps = store.list_steps(jid)
                self.assertEqual(steps[0]["kind"], "audio_wav")
            finally:
                engine.pause_queue(False)
                engine.shutdown()
                store.close()


if __name__ == "__main__":
    unittest.main()
