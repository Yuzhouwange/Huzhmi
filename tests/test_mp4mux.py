"""`mp4mux` 的单元测试：用自己拼出来的最小 fMP4 验证重新装箱结果（不联网）。

真实 B 站分片是 H.264/AAC 的 fMP4，但重新装箱只关心「box 结构 + 样本表」，
所以这里用假样本数据就能覆盖：轨 ID 去重、stts/stsc/stsz/stco 一致性、
elst 重写、以及 mdat 里的字节顺序。
"""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from huzhmi import mp4mux


def box(typ: bytes, *payload: bytes) -> bytes:
    body = b"".join(payload)
    return struct.pack(">I", len(body) + 8) + typ + body


def full(typ: bytes, version: int, flags: int, *payload: bytes) -> bytes:
    head = struct.pack(">I", (version << 24) | flags)
    return box(typ, head, *payload)


def make_moof(samples: list[int], start: int,
              base_flags: int = 0x02000000) -> tuple[bytes, bytes]:
    """构造一个 moof + mdat：每个样本的大小来自 samples。"""
    payload = b"".join(bytes([index % 256]) * size
                       for index, size in enumerate(samples))

    def build(data_offset: int) -> bytes:
        # trun：逐样本给 size，data-offset 相对 moof 起点；时长/关键帧走 tfhd 默认值
        trun_body = (struct.pack(">Ii", len(samples), data_offset)
                     + b"".join(struct.pack(">I", size) for size in samples))
        trun = full(b"trun", 0, 0x000001 | 0x000200, trun_body)
        tfhd = full(b"tfhd", 0, 0x020038,
                    struct.pack(">I", 1),     # track_id
                    struct.pack(">I", 100),   # default duration
                    struct.pack(">I", 0),     # default size（未用）
                    struct.pack(">I", base_flags))
        tfdt = full(b"tfdt", 1, 0, struct.pack(">Q", start))
        mfhd = full(b"mfhd", 0, 0, struct.pack(">I", start + 1))
        return box(b"moof", mfhd, box(b"traf", tfhd, tfdt, trun))

    moof = build(0)
    # mdat 头 8 字节，所以 moof 后面紧跟的数据从 len(moof)+8 开始
    moof = build(len(moof) + 8)
    return moof, box(b"mdat", payload)


def make_init(kind: bytes, with_elst: bool = False) -> bytes:
    entry = box(b"avc1" if kind == b"vide" else b"mp4a", b"\x00" * 32)
    stsd = full(b"stsd", 0, 0, struct.pack(">I", 1), entry)
    stbl = box(b"stbl", stsd,
               full(b"stts", 0, 0, struct.pack(">I", 0)),
               full(b"stsc", 0, 0, struct.pack(">I", 0)),
               full(b"stsz", 0, 0, struct.pack(">II", 0, 0)),
               full(b"stco", 0, 0, struct.pack(">I", 0)))
    header = full(b"vmhd", 0, 1, b"\x00" * 8) if kind == b"vide" else \
        full(b"smhd", 0, 0, b"\x00" * 4)
    dinf = box(b"dinf", full(b"dref", 0, 0, struct.pack(">I", 1),
                             full(b"url ", 0, 1)))
    minf = box(b"minf", header, dinf, stbl)
    mdhd = full(b"mdhd", 0, 0, struct.pack(">IIIIHH", 0, 0, 1000, 0, 0x55C4, 0))
    hdlr = full(b"hdlr", 0, 0, b"\x00" * 4, kind, b"\x00" * 12, b"\x00")
    mdia = box(b"mdia", mdhd, hdlr, minf)
    tkhd = full(b"tkhd", 0, 3,
                struct.pack(">IIIII", 0, 0, 1, 0, 0),
                b"\x00" * 8, struct.pack(">hhhH", 0, 0, 0, 0),
                struct.pack(">9i", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000),
                struct.pack(">II", 320 << 16, 240 << 16))
    parts = [tkhd]
    if with_elst:
        parts.append(box(b"edts", full(b"elst", 0, 0, struct.pack(">I", 1),
                                       struct.pack(">Iii", 0, 1600, 0x10000))))
    parts.append(mdia)
    trak = box(b"trak", *parts)
    mvhd = full(b"mvhd", 0, 0, struct.pack(">IIII", 0, 0, 1000, 0),
                struct.pack(">iH", 0x10000, 0x100), b"\x00" * 10,
                struct.pack(">9i", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000),
                b"\x00" * 24, struct.pack(">I", 2))
    trex = full(b"trex", 0, 0, struct.pack(">IIIII", 1, 1, 100, 0, 0x02000000))
    return box(b"ftyp", b"isom", struct.pack(">I", 512), b"isomiso2mp41") + \
        box(b"moov", mvhd, trak, box(b"mvex", trex))


def write_m4s(path: Path, kind: bytes, chunks: list[list[int]],
              with_elst: bool = False) -> None:
    data = bytearray(make_init(kind, with_elst))
    ticks = 0
    for sizes in chunks:
        moof, mdat = make_moof(sizes, ticks)
        data += moof + mdat
        ticks += len(sizes) * 100      # 每个样本默认 100 tick
    path.write_bytes(bytes(data))


def parse_stbl(data: bytes) -> dict:
    """极简 MP4 解析：只取断言需要的字段。"""

    def boxes(buf: bytes, start: int, end: int):
        off = start
        while off + 8 <= end:
            size = struct.unpack_from(">I", buf, off)[0]
            typ = buf[off + 4:off + 8]
            hdr = 8
            if size == 1:
                size = struct.unpack_from(">Q", buf, off + 8)[0]
                hdr = 16
            yield typ, off, size, hdr
            off += size

    out: dict = {"tracks": [], "boxes": [t.decode() for t, *_ in boxes(data, 0, len(data))]}
    mdat_start = mdat_end = 0
    for typ, off, size, hdr in boxes(data, 0, len(data)):
        if typ == b"mdat":
            mdat_start, mdat_end = off + hdr, off + size
    out["mdat"] = (mdat_start, mdat_end)
    for typ, off, size, hdr in boxes(data, 0, len(data)):
        if typ != b"moov":
            continue
        for t2, o2, s2, h2 in boxes(data, off + hdr, off + size):
            if t2 != b"trak":
                continue
            track = {"id": 0, "kind": "", "elst": None, "sample_sizes": [],
                     "stts": [], "stsc": [], "stco": [], "timescale": 0,
                     "mdhd_duration": 0}
            for t3, o3, s3, h3 in boxes(data, o2 + h2, o2 + s2):
                if t3 == b"tkhd":
                    track["id"] = struct.unpack_from(">I", data, o3 + 20)[0]
                elif t3 == b"edts":
                    for t4, o4, s4, h4 in boxes(data, o3 + h3, o3 + s3):
                        if t4 == b"elst":
                            track["elst"] = struct.unpack_from(
                                ">Iii", data, o4 + h4 + 8)
                elif t3 == b"mdia":
                    for t4, o4, s4, h4 in boxes(data, o3 + h3, o3 + s3):
                        if t4 == b"mdhd":
                            track["timescale"] = struct.unpack_from(
                                ">I", data, o4 + 20)[0]
                            track["mdhd_duration"] = struct.unpack_from(
                                ">I", data, o4 + 24)[0]
                        elif t4 == b"hdlr":
                            track["kind"] = data[o4 + h4 + 8:o4 + h4 + 12].decode()
                        elif t4 == b"minf":
                            for t5, o5, s5, h5 in boxes(data, o4 + h4, o4 + s4):
                                if t5 != b"stbl":
                                    continue
                                for t6, o6, s6, h6 in boxes(data, o5 + h5, o5 + s5):
                                    p = o6 + h6 + 4
                                    if t6 == b"stsz":
                                        ss, cnt = struct.unpack_from(">II", data, p)
                                        track["sample_sizes"] = (
                                            [ss] * cnt if ss else
                                            list(struct.unpack_from(f">{cnt}I",
                                                                    data, p + 8)))
                                    elif t6 == b"stts":
                                        n = struct.unpack_from(">I", data, p)[0]
                                        track["stts"] = [
                                            struct.unpack_from(">II", data, p + 4 + i * 8)
                                            for i in range(n)]
                                    elif t6 == b"stsc":
                                        n = struct.unpack_from(">I", data, p)[0]
                                        track["stsc"] = [
                                            struct.unpack_from(">III", data, p + 4 + i * 12)
                                            for i in range(n)]
                                    elif t6 == b"stco":
                                        n = struct.unpack_from(">I", data, p)[0]
                                        track["stco"] = list(
                                            struct.unpack_from(f">{n}I", data, p + 4))
            out["tracks"].append(track)
    return out


class MuxTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_boxes_and_ids_are_unique(self) -> None:
        video = self.dir / "v.m4s"
        audio = self.dir / "a.m4s"
        write_m4s(video, b"vide", [[10, 20], [30, 40]], with_elst=True)
        write_m4s(audio, b"soun", [[5, 6, 7]])
        out = self.dir / "out.mp4"

        stats = mp4mux.mux(video, audio, out)
        self.assertTrue(out.exists())
        self.assertEqual(stats["video_samples"], 4)
        self.assertEqual(stats["audio_samples"], 3)
        self.assertTrue(stats["has_audio"])

        parsed = parse_stbl(out.read_bytes())
        self.assertEqual(parsed["boxes"], ["ftyp", "moov", "mdat"])
        ids = [t["id"] for t in parsed["tracks"]]
        self.assertEqual(ids, [1, 2], "两个源文件都是 track_id=1，输出必须重排")

    def test_sample_tables_are_consistent(self) -> None:
        video = self.dir / "v.m4s"
        audio = self.dir / "a.m4s"
        write_m4s(video, b"vide", [[10, 20, 30], [40, 50]])
        write_m4s(audio, b"soun", [[5, 6], [7, 8]])
        out = self.dir / "out.mp4"
        mp4mux.mux(video, audio, out)

        parsed = parse_stbl(out.read_bytes())
        mdat_start, mdat_end = parsed["mdat"]
        for track in parsed["tracks"]:
            # stsz 的样本数与源一致
            expected = 5 if track["kind"] == "vide" else 4
            self.assertEqual(len(track["sample_sizes"]), expected)
            # mdhd duration == stts 累计
            self.assertEqual(track["mdhd_duration"],
                             sum(c * d for c, d in track["stts"]))
            # stsc 覆盖全部样本
            per_chunk = []
            for i, (first, count, _sdi) in enumerate(track["stsc"]):
                nxt = track["stsc"][i + 1][0] if i + 1 < len(track["stsc"]) \
                    else len(track["stco"]) + 1
                per_chunk.extend([count] * (nxt - first))
            self.assertEqual(sum(per_chunk), expected)
            # 每块的偏移 + 大小都落在 mdat 内
            idx = 0
            for chunk_index, offset in enumerate(track["stco"]):
                n = per_chunk[chunk_index]
                size = sum(track["sample_sizes"][idx:idx + n])
                self.assertGreaterEqual(offset, mdat_start)
                self.assertLessEqual(offset + size, mdat_end)
                idx += n
            self.assertEqual(sum(track["sample_sizes"]),
                             sum(track["sample_sizes"]))

    def test_mdat_payload_matches_sources(self) -> None:
        video = self.dir / "v.m4s"
        write_m4s(video, b"vide", [[3, 4, 5]])
        out = self.dir / "out.mp4"
        mp4mux.mux(video, None, out)
        data = out.read_bytes()
        start, end = parse_stbl(data)["mdat"]
        # 假样本按 (index % 256) 填充：3+4+5 个字节
        self.assertEqual(end - start, 12)
        self.assertEqual(data[start:start + 3], b"\x00\x00\x00")
        self.assertEqual(data[start + 3:start + 7], b"\x01\x01\x01\x01")

    def test_elst_is_rewritten_with_real_duration(self) -> None:
        video = self.dir / "v.m4s"
        write_m4s(video, b"vide", [[10, 20]], with_elst=True)
        out = self.dir / "out.mp4"
        mp4mux.mux(video, None, out)
        track = parse_stbl(out.read_bytes())["tracks"][0]
        self.assertIsNotNone(track["elst"])
        segment_duration, media_time, rate = track["elst"]
        # 2 个样本 × 默认 100 tick / timescale 1000 → 200ms → movie timescale 1000
        self.assertEqual(segment_duration, 200)
        self.assertEqual(media_time, 1600, "源文件的预滚偏移必须保留")
        self.assertEqual(rate, 0x10000)

    def test_video_only_is_allowed(self) -> None:
        video = self.dir / "v.m4s"
        write_m4s(video, b"vide", [[10]])
        out = self.dir / "out.mp4"
        stats = mp4mux.mux(video, None, out)
        self.assertFalse(stats["has_audio"])
        self.assertEqual(stats["audio_samples"], 0)

    def test_missing_moov_raises(self) -> None:
        broken = self.dir / "broken.m4s"
        broken.write_bytes(box(b"ftyp", b"isom", b"\x00" * 4))
        with self.assertRaises(mp4mux.MuxError):
            mp4mux.mux(broken, None, self.dir / "x.mp4")

    def test_empty_fragments_raise(self) -> None:
        video = self.dir / "v.m4s"
        video.write_bytes(make_init(b"vide"))
        with self.assertRaises(mp4mux.MuxError):
            mp4mux.mux(video, None, self.dir / "x.mp4")


if __name__ == "__main__":
    unittest.main()
