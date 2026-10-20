"""媒体转换层：注册表、纯 Python 音轨提取、ffmpeg 缺工具的报错。"""

from __future__ import annotations

import os
import struct
import tempfile
import unittest
from pathlib import Path

from douyinkit import media, mp4mux


# ---------------------------------------------------------------------------
# 造一个最小的 fMP4（复用 test_mp4mux 的思路：只关心 box 结构和样本表）
# ---------------------------------------------------------------------------

def box(typ: bytes, *payload: bytes) -> bytes:
    body = b"".join(payload)
    return struct.pack(">I", len(body) + 8) + typ + body


def full(typ: bytes, version: int, flags: int, *payload: bytes) -> bytes:
    return box(typ, struct.pack(">I", (version << 24) | flags), *payload)


def make_init(kind: bytes) -> bytes:
    entry = box(b"avc1" if kind == b"vide" else b"mp4a", b"\x00" * 32)
    stsd = full(b"stsd", 0, 0, struct.pack(">I", 1), entry)
    stbl = box(b"stbl", stsd,
               full(b"stts", 0, 0, struct.pack(">I", 0)),
               full(b"stsc", 0, 0, struct.pack(">I", 0)),
               full(b"stsz", 0, 0, struct.pack(">II", 0, 0)),
               full(b"stco", 0, 0, struct.pack(">I", 0)))
    header = (full(b"vmhd", 0, 1, b"\x00" * 8) if kind == b"vide"
              else full(b"smhd", 0, 0, b"\x00" * 4))
    dinf = box(b"dinf", full(b"dref", 0, 0, struct.pack(">I", 1),
                             full(b"url ", 0, 1)))
    minf = box(b"minf", header, dinf, stbl)
    mdhd = full(b"mdhd", 0, 0, struct.pack(">IIIIHH", 0, 0, 1000, 0, 0x55C4, 0))
    hdlr = full(b"hdlr", 0, 0, b"\x00" * 4, kind, b"\x00" * 12, b"\x00")
    mdia = box(b"mdia", mdhd, hdlr, minf)
    tkhd = full(b"tkhd", 0, 3, struct.pack(">IIIII", 0, 0, 1, 0, 0),
                b"\x00" * 8, struct.pack(">hhhH", 0, 0, 0, 0),
                struct.pack(">9i", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000),
                struct.pack(">II", 320 << 16, 240 << 16))
    trak = box(b"trak", tkhd, mdia)
    mvhd = full(b"mvhd", 0, 0, struct.pack(">IIII", 0, 0, 1000, 0),
                struct.pack(">iH", 0x10000, 0x100), b"\x00" * 10,
                struct.pack(">9i", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000),
                b"\x00" * 24, struct.pack(">I", 2))
    trex = full(b"trex", 0, 0, struct.pack(">IIIII", 1, 1, 100, 0, 0x02000000))
    return (box(b"ftyp", b"isom", struct.pack(">I", 512), b"isomiso2mp41")
            + box(b"moov", mvhd, trak, box(b"mvex", trex)))


def make_moof(sizes: list[int], start: int) -> tuple[bytes, bytes]:
    payload = b"".join(bytes([index % 256]) * size
                       for index, size in enumerate(sizes))

    def build(data_offset: int) -> bytes:
        trun_body = (struct.pack(">Ii", len(sizes), data_offset)
                     + b"".join(struct.pack(">I", size) for size in sizes))
        trun = full(b"trun", 0, 0x000001 | 0x000200, trun_body)
        tfhd = full(b"tfhd", 0, 0x020038, struct.pack(">I", 1),
                    struct.pack(">I", 100), struct.pack(">I", 0),
                    struct.pack(">I", 0x02000000))
        tfdt = full(b"tfdt", 1, 0, struct.pack(">Q", start))
        mfhd = full(b"mfhd", 0, 0, struct.pack(">I", start + 1))
        return box(b"moof", mfhd, box(b"traf", tfhd, tfdt, trun))

    moof = build(0)
    moof = build(len(moof) + 8)
    return moof, box(b"mdat", payload)


def write_fmp4(path: Path, kind: bytes, chunks: list[list[int]]) -> None:
    data = bytearray(make_init(kind))
    ticks = 0
    for sizes in chunks:
        moof, mdat = make_moof(sizes, ticks)
        data += moof + mdat
        ticks += len(sizes) * 100
    path.write_bytes(bytes(data))


class TransformRegistryTest(unittest.TestCase):
    def test_builtin_names(self) -> None:
        names = media.names()
        self.assertIn("audio_m4a", names)
        self.assertIn("audio_mp3", names)
        self.assertIn("audio_flac", names)

    def test_unknown_op_raises(self) -> None:
        with self.assertRaises(media.TransformError):
            media.get("nope")

    def test_describe_flags_ffmpeg_ops(self) -> None:
        rows = {row["name"]: row for row in media.describe({"ffmpeg_path": ""})}
        self.assertFalse(rows["audio_m4a"]["needs"])
        self.assertTrue(rows["audio_m4a"]["available"], "纯 Python 永远可用")
        self.assertEqual(rows["audio_mp3"]["needs"], "ffmpeg")
        if media.ffmpeg.find_ffmpeg({}) is None:
            self.assertFalse(rows["audio_mp3"]["available"])
            self.assertIn("ffmpeg", rows["audio_mp3"]["reason"])


class AudioExtractTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.audio = self.root / "audio.m4s"
        self.video = self.root / "video.m4s"
        write_fmp4(self.audio, b"soun", [[10, 20, 30], [40, 50]])
        write_fmp4(self.video, b"vide", [[100, 200], [300]])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_mux_then_extract_keeps_audio(self) -> None:
        merged = self.root / "merged.mp4"
        stats = mp4mux.mux(self.video, self.audio, merged)
        self.assertEqual(stats["audio_samples"], 5)

        out = self.root / "out.m4a"
        result = media.get("audio_m4a").run(merged, out)
        self.assertTrue(out.is_file())
        self.assertTrue(result["lossless"])
        self.assertEqual(result["samples"], 5)

        info = mp4mux.probe(out)
        kinds = [t["kind"] for t in info["tracks"]]
        self.assertEqual(kinds, ["soun"], "提取后应该只剩音轨")
        self.assertEqual(info["tracks"][0]["samples"], 5)

    def test_extract_from_progressive_mp4(self) -> None:
        """自己 mux 出来的就是普通 MP4（没有 moof），走样本表那条路。"""
        merged = self.root / "merged.mp4"
        mp4mux.mux(self.video, self.audio, merged)
        self.assertNotIn("moof", mp4mux.probe(merged)["boxes"])

        out = self.root / "prog.m4a"
        media.get("audio_m4a").run(merged, out)
        self.assertGreater(out.stat().st_size, 0)

    def test_video_only_file_errors_clearly(self) -> None:
        merged = self.root / "silent.mp4"
        mp4mux.mux(self.video, None, merged)
        with self.assertRaises(mp4mux.MuxError) as ctx:
            media.get("audio_m4a").run(merged, self.root / "x.m4a")
        self.assertIn("没有音轨", str(ctx.exception))

    def test_missing_source_errors(self) -> None:
        with self.assertRaises(media.TransformError):
            media.get("audio_m4a").run(self.root / "nope.mp4", self.root / "o.m4a")

    def test_ffmpeg_op_without_tool(self) -> None:
        if media.ffmpeg.find_ffmpeg({}) is not None:
            self.skipTest("这台机器有 ffmpeg")
        with self.assertRaises(media.ToolMissing) as ctx:
            media.get("audio_mp3").run(self.audio, self.root / "x.mp3",
                                       cfg={"ffmpeg_path": ""})
        self.assertIn("ffmpeg", str(ctx.exception))


class StepStoreTest(unittest.TestCase):
    def test_steps_roundtrip(self) -> None:
        from douyinkit.store import Store

        with tempfile.TemporaryDirectory() as tmp:
            store = Store(str(Path(tmp) / "t.db"))
            try:
                jid = store.create_job("u", "k")
                store.add_step(jid, 0, "audio_m4a", {"src": "a.mp4"})
                store.update_step(jid, 0, status="running", progress=0.4)
                steps = store.list_steps(jid)
                self.assertEqual(len(steps), 1)
                self.assertEqual(steps[0]["kind"], "audio_m4a")
                self.assertEqual(steps[0]["status"], "running")
                self.assertAlmostEqual(steps[0]["progress"], 0.4, places=3)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
