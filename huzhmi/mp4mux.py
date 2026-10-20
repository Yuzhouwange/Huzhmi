"""把 B 站 DASH 的 fMP4（`.m4s`）音视频合成一个普通 MP4。

为什么不调 ffmpeg：
  - 本项目承诺「纯标准库、不要 pip 装东西」，ffmpeg 也不一定在用户机器上；
  - 这条链路两路都是 H.264/AAC 的 fMP4，本来就是**不转码**，只是把分片
    重新装箱成索引完整的普通 MP4，用 Python 完全做得到（几百行）。

做法：
  1. 读 init segment（`moov`）拿到每轨的 timescale / stsd（编码参数原样保留）
     / edts（B 站用它做音画对齐，原样保留）
  2. 扫所有 `moof`，把 trun 里每个 sample 的（文件偏移、大小、时长、flags、
     合成时间偏移）摊平成一张样本表；默认值按 tfhd → trex 顺序回落
  3. 按时间戳把两路分片交错排序，一个分片就是输出文件里的一「块」（chunk）
  4. 写 `ftyp` + `moov`（stsd/stts/ctts/stss/stsc/stsz/stco）+ `mdat`

产出是普通 MP4：拖进任何播放器都能播、能拖进度条，也不需要联网再解封装。
样本数据全程按块读写，不把整部视频读进内存。
"""

from __future__ import annotations

import struct
from pathlib import Path

__all__ = ["MuxError", "mux", "probe"]


class MuxError(Exception):
    """合成失败，消息直接给用户看。"""


# 需要进到内层继续拆的容器 box
_CONTAINERS = {
    b"moov", b"trak", b"mdia", b"minf", b"stbl", b"mvex", b"moof", b"traf",
    b"dinf", b"edts", b"mfra", b"udta",
}

# trun / tfhd 的 flag 位
_TFHD_BASE_DATA_OFFSET = 0x000001
_TFHD_SAMPLE_DESC_INDEX = 0x000002
_TFHD_DEFAULT_DURATION = 0x000008
_TFHD_DEFAULT_SIZE = 0x000010
_TFHD_DEFAULT_FLAGS = 0x000020
_TFHD_DEFAULT_BASE_IS_MOOF = 0x020000

_TRUN_DATA_OFFSET = 0x000001
_TRUN_FIRST_SAMPLE_FLAGS = 0x000004
_TRUN_SAMPLE_DURATION = 0x000100
_TRUN_SAMPLE_SIZE = 0x000200
_TRUN_SAMPLE_FLAGS = 0x000400
_TRUN_SAMPLE_CTS = 0x000800

# sample flags 里的「非关键帧」位
_SAMPLE_IS_NON_SYNC = 0x00010000


def _u16(buf: bytes, off: int) -> int:
    return struct.unpack_from(">H", buf, off)[0]


def _u32(buf: bytes, off: int) -> int:
    return struct.unpack_from(">I", buf, off)[0]


def _i32(buf: bytes, off: int) -> int:
    return struct.unpack_from(">i", buf, off)[0]


def _i64(buf: bytes, off: int) -> int:
    return struct.unpack_from(">q", buf, off)[0]


def _u64(buf: bytes, off: int) -> int:
    return struct.unpack_from(">Q", buf, off)[0]


def _iter_boxes(fh, start: int, end: int):
    """按顺序产出 (type, offset, size, header_len)。"""
    pos = start
    while pos + 8 <= end:
        fh.seek(pos)
        head = fh.read(16)
        if len(head) < 8:
            return
        size = _u32(head, 0)
        typ = head[4:8]
        hdr = 8
        if size == 1:
            if len(head) < 16:
                return
            size = _u64(head, 8)
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr or pos + size > end:
            return
        yield typ, pos, size, hdr
        pos += size


def _payload_start(box: bytes) -> int:
    """一个 box 自身的 header 长度（8 或 16，`size==1` 时是 largesize）。"""
    if len(box) >= 16 and _u32(box, 0) == 1:
        return 16
    return 8


def _read(fh, pos: int, size: int) -> bytes:
    fh.seek(pos)
    data = fh.read(size)
    if len(data) != size:
        raise MuxError("文件被截断，读不到完整的分片数据")
    return data


class _Track:
    __slots__ = ("track_id", "movie_id", "timescale", "kind", "stsd",
                 "width", "height", "trex", "chunks", "duration", "samples",
                 "edit_media_time", "stbl")

    def __init__(self, track_id: int, timescale: int, kind: bytes) -> None:
        self.track_id = track_id
        # 视频/音频是两个独立文件，track_id 都可能是 1；输出里必须重排成唯一值
        self.movie_id = track_id
        self.timescale = timescale
        self.kind = kind
        self.stsd: bytes = b""
        # 源文件用 elst 的 media_time 抵消片头预滚（B 站视频是 1600 tick = 100ms）
        self.edit_media_time: int | None = None
        self.width = 0
        self.height = 0
        self.trex: dict = {}
        self.chunks: list[dict] = []   # {"start": float, "samples": [...], "offset": int}
        # 普通 MP4 的样本表（stsz/stco/stsc/stts），分片 MP4 没有
        self.stbl: dict[bytes, bytes] = {}
        self.duration = 0
        self.samples = 0

    @property
    def is_video(self) -> bool:
        return self.kind == b"vide"


def _parse_trak(trak: bytes) -> _Track | None:
    track_id = 0
    timescale = 0
    kind = b""
    stsd = b""
    edit_media_time: int | None = None
    width = height = 0
    tables: dict[bytes, bytes] = {}

    class _Buf:
        def __init__(self, data: bytes) -> None:
            self.data = data
            self.pos = 0

        def seek(self, pos: int) -> None:
            self.pos = pos

        def read(self, size: int) -> bytes:
            return self.data[self.pos:self.pos + size]

    fh = _Buf(trak)
    for typ, off, size, hdr in _iter_boxes(fh, _payload_start(trak), len(trak)):
        inner_start, inner_end = off + hdr, off + size
        if typ == b"tkhd":
            version = trak[off + hdr]
            base = off + hdr + 4
            if version == 1:
                track_id = _u32(trak, base + 16)
                width = _u32(trak, off + size - 8)
                height = _u32(trak, off + size - 4)
            else:
                track_id = _u32(trak, base + 8)
                width = _u32(trak, off + size - 8)
                height = _u32(trak, off + size - 4)
        elif typ == b"edts":
            for t2, o2, s2, h2 in _iter_boxes(fh, inner_start, inner_end):
                if t2 != b"elst":
                    continue
                version = trak[o2 + h2]
                count = _u32(trak, o2 + h2 + 4)
                if count >= 1:
                    # v0 条目 12 字节（4+4+4），v1 是 20 字节（8+8+4）
                    entry = o2 + h2 + 8
                    media_time = (_i32(trak, entry + 4) if version == 0
                                  else _i64(trak, entry + 8))
                    edit_media_time = media_time
        elif typ == b"mdia":
            for t2, o2, s2, h2 in _iter_boxes(fh, inner_start, inner_end):
                if t2 == b"mdhd":
                    version = trak[o2 + h2]
                    base = o2 + h2 + 4
                    timescale = _u32(trak, base + (16 if version == 1 else 8))
                elif t2 == b"hdlr":
                    kind = trak[o2 + h2 + 8:o2 + h2 + 12]
                elif t2 == b"minf":
                    for t3, o3, s3, h3 in _iter_boxes(fh, o2 + h2, o2 + s2):
                        if t3 == b"stbl":
                            for t4, o4, s4, _ in _iter_boxes(fh, o3 + h3, o3 + s3):
                                if t4 == b"stsd":
                                    stsd = trak[o4:o4 + s4]
                                elif t4 in (b"stsz", b"stco", b"co64", b"stsc",
                                            b"stts"):
                                    tables[t4] = trak[o4:o4 + s4]
    if not track_id or not timescale or not stsd:
        return None
    track = _Track(track_id, timescale, kind)
    track.stbl = tables
    track.stsd = stsd
    track.edit_media_time = edit_media_time
    track.width = width >> 16
    track.height = height >> 16
    return track


def _parse_moov(buf: bytes, start: int, size: int, hdr: int) -> tuple[dict, int]:
    """返回 ({track_id: _Track}, movie_timescale)。"""
    tracks: dict[int, _Track] = {}
    movie_timescale = 1000
    trex: dict[int, dict] = {}

    class _Buf:
        def __init__(self, data: bytes) -> None:
            self.data = data
            self.pos = 0

        def seek(self, pos: int) -> None:
            self.pos = pos

        def read(self, n: int) -> bytes:
            return self.data[self.pos:self.pos + n]

    fh = _Buf(buf)
    for typ, off, bsize, bhdr in _iter_boxes(fh, start + hdr, start + size):
        if typ == b"mvhd":
            version = buf[off + bhdr]
            base = off + bhdr + 4
            movie_timescale = _u32(buf, base + (16 if version == 1 else 8))
        elif typ == b"trak":
            track = _parse_trak(buf[off:off + bsize])
            if track is not None:
                tracks[track.track_id] = track
        elif typ == b"mvex":
            for t2, o2, s2, h2 in _iter_boxes(fh, off + bhdr, off + bsize):
                if t2 == b"trex":
                    tid = _u32(buf, o2 + h2 + 4)
                    trex[tid] = {
                        "sdi": _u32(buf, o2 + h2 + 8),
                        "duration": _u32(buf, o2 + h2 + 12),
                        "size": _u32(buf, o2 + h2 + 16),
                        "flags": _u32(buf, o2 + h2 + 20),
                    }
    for tid, values in trex.items():
        if tid in tracks:
            tracks[tid].trex = values
    return tracks, movie_timescale


def _parse_fragments(fh, tracks: dict[int, _Track]) -> None:
    """扫顶层 moof，把样本摊进各 track 的 chunks。"""
    fh.seek(0, 2)
    total = fh.tell()
    for typ, off, size, hdr in _iter_boxes(fh, 0, total):
        if typ != b"moof":
            continue
        moof = _read(fh, off, size)
        moof_start = off
        for t2, o2, s2, h2 in _iter_boxes(_BytesFH(moof), _payload_start(moof),
                                         len(moof)):
            if t2 != b"traf":
                continue
            _parse_traf(moof, o2 + h2, o2 + s2, moof_start, tracks)


class _BytesFH:
    """把 bytes 伪装成可 seek 的文件，复用 _iter_boxes。"""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def seek(self, pos: int, whence: int = 0) -> None:
        self._pos = pos if whence == 0 else self._pos + pos

    def read(self, n: int) -> bytes:
        data = self._data[self._pos:self._pos + n]
        self._pos += len(data)
        return data

    def tell(self) -> int:
        return self._pos


def _parse_traf(moof: bytes, start: int, end: int, moof_start: int,
                tracks: dict[int, _Track]) -> None:
    fh = _BytesFH(moof)
    tfhd_off = tfhd_size = tfdt_off = None
    truns: list[tuple[int, int]] = []
    for typ, off, size, hdr in _iter_boxes(fh, start, end):
        if typ == b"tfhd":
            tfhd_off, tfhd_size = off, size
        elif typ == b"tfdt":
            tfdt_off = off
        elif typ == b"trun":
            truns.append((off, size))
    if tfhd_off is None or not truns:
        return

    flags = _u32(moof, tfhd_off + 8) & 0xFFFFFF
    track_id = _u32(moof, tfhd_off + 12)
    track = tracks.get(track_id)
    if track is None:
        return

    pos = tfhd_off + 16
    base_data_offset = None
    sdi = duration = sample_size = sample_flags = None
    if flags & _TFHD_BASE_DATA_OFFSET:
        base_data_offset = _u64(moof, pos)
        pos += 8
    if flags & _TFHD_SAMPLE_DESC_INDEX:
        sdi = _u32(moof, pos)
        pos += 4
    if flags & _TFHD_DEFAULT_DURATION:
        duration = _u32(moof, pos)
        pos += 4
    if flags & _TFHD_DEFAULT_SIZE:
        sample_size = _u32(moof, pos)
        pos += 4
    if flags & _TFHD_DEFAULT_FLAGS:
        sample_flags = _u32(moof, pos)
        pos += 4
    del sdi, pos, tfhd_size

    defaults = track.trex
    if duration is None:
        duration = defaults.get("duration") or 0
    if sample_size is None:
        sample_size = defaults.get("size") or 0
    if sample_flags is None:
        sample_flags = defaults.get("flags") or 0

    base = moof_start
    if base_data_offset is not None:
        base = base_data_offset
    elif flags & _TFHD_DEFAULT_BASE_IS_MOOF:
        base = moof_start

    decode_time = 0
    if tfdt_off is not None:
        version = moof[tfdt_off + 8]
        decode_time = (_u64(moof, tfdt_off + 12) if version == 1
                       else _u32(moof, tfdt_off + 12))

    for run_off, run_size in truns:
        tflags = _u32(moof, run_off + 8) & 0xFFFFFF
        count = _u32(moof, run_off + 12)
        p = run_off + 16
        data_offset = 0
        first_flags = None
        if tflags & _TRUN_DATA_OFFSET:
            data_offset = _i32(moof, p)
            p += 4
        if tflags & _TRUN_FIRST_SAMPLE_FLAGS:
            first_flags = _u32(moof, p)
            p += 4

        # trun 里可选字段是**逐样本**排列的（每个 sample 一组），不是分列数组
        cursor = base + data_offset
        chunk = {"start": decode_time / track.timescale, "samples": [],
                 "offset": cursor}
        for index in range(count):
            if tflags & _TRUN_SAMPLE_DURATION:
                dur = _u32(moof, p)
                p += 4
            else:
                dur = duration
            if tflags & _TRUN_SAMPLE_SIZE:
                ssize = _u32(moof, p)
                p += 4
            else:
                ssize = sample_size
            if tflags & _TRUN_SAMPLE_FLAGS:
                sflags = _u32(moof, p)
                p += 4
            elif index == 0 and first_flags is not None:
                sflags = first_flags
            else:
                sflags = sample_flags
            if tflags & _TRUN_SAMPLE_CTS:
                cts = (_i32(moof, p) if _u32(moof, run_off + 8) & 0x1000000
                       else _u32(moof, p))
                p += 4
            else:
                cts = 0
            if not ssize:
                raise MuxError("分片缺少样本长度（tfhd/trun 都没给）")
            chunk["samples"].append({
                "offset": cursor,
                "size": ssize,
                "duration": dur or 0,
                "flags": sflags,
                "cts": cts,
            })
            cursor += ssize
            decode_time += dur or 0
        if p > run_off + run_size:
            raise MuxError("分片头部损坏（trun 越界）")
        track.chunks.append(chunk)
        track.duration = max(track.duration, decode_time)
        track.samples += count


def _attach_progressive_samples(track: _Track) -> None:
    """普通 MP4（非分片）的样本在 mdat 里，靠 stbl 的样本表定位。

    还原成和分片一样的 `chunks` 结构，后面的写文件逻辑就不用分两条路。
    """
    if track.chunks or not track.stbl:
        return
    stsz = track.stbl.get(b"stsz")
    stco = track.stbl.get(b"stco") or track.stbl.get(b"co64")
    stsc = track.stbl.get(b"stsc")
    if not (stsz and stco and stsc):
        return

    uniform = _u32(stsz, 12)
    count = _u32(stsz, 16)
    if not count:
        return
    if uniform:
        sizes = [uniform] * count
    else:
        sizes = [_u32(stsz, 20 + 4 * i) for i in range(count)]

    if stco[4:8] == b"co64":
        chunk_count = _u32(stco, 12)
        offsets = [_u64(stco, 16 + 8 * i) for i in range(chunk_count)]
    else:
        chunk_count = _u32(stco, 12)
        offsets = [_u32(stco, 16 + 4 * i) for i in range(chunk_count)]
    if not offsets:
        return

    entries: list[tuple[int, int]] = []
    for i in range(_u32(stsc, 12)):
        base = 16 + 12 * i
        entries.append((_u32(stsc, base), _u32(stsc, base + 4)))
    per_chunk = [0] * len(offsets)
    for index, (first, samples_per_chunk) in enumerate(entries):
        last = (entries[index + 1][0] - 1 if index + 1 < len(entries)
                else len(offsets))
        for chunk_no in range(first, last + 1):
            if 1 <= chunk_no <= len(per_chunk):
                per_chunk[chunk_no - 1] = samples_per_chunk

    durations: list[int] = []
    stts = track.stbl.get(b"stts")
    if stts:
        for i in range(_u32(stts, 12)):
            delta_count = _u32(stts, 16 + 8 * i)
            delta = _u32(stts, 20 + 8 * i)
            durations.extend([delta] * delta_count)

    pos = 0
    ticks = 0
    for chunk_no, offset in enumerate(offsets):
        chunk = {"start": ticks / track.timescale, "samples": [], "offset": offset}
        cursor = offset
        for _ in range(per_chunk[chunk_no]):
            if pos >= count:
                break
            size = sizes[pos]
            duration = durations[pos] if pos < len(durations) else 0
            chunk["samples"].append({"offset": cursor, "size": size,
                                     "duration": duration, "cts": 0, "flags": 0})
            cursor += size
            ticks += duration
            pos += 1
        if chunk["samples"]:
            track.chunks.append(chunk)
    track.samples = pos
    track.duration = ticks


def _parse_init(fh) -> tuple[dict[int, _Track], int]:
    fh.seek(0, 2)
    total = fh.tell()
    for typ, off, size, hdr in _iter_boxes(fh, 0, total):
        if typ == b"moov":
            tracks, movie_timescale = _parse_moov(_read(fh, off, size), 0, size, hdr)
            for track in tracks.values():
                _attach_progressive_samples(track)
            return tracks, movie_timescale
    raise MuxError("这不是可识别的 m4s：找不到 moov（初始化段）")


# --------------------------------------------------------------------------
# 写 MP4
# --------------------------------------------------------------------------


def _box(typ: bytes, *payload: bytes) -> bytes:
    body = b"".join(payload)
    return struct.pack(">I", len(body) + 8) + typ + body


def _full_box(typ: bytes, version: int, flags: int, *payload: bytes) -> bytes:
    head = struct.pack(">I", (version << 24) | (flags & 0xFFFFFF))
    return _box(typ, head, *payload)


def _compress_runs(values: list[int], max_entries: int = 0) -> list[tuple[int, int]]:
    """把「每样本一个值」压成 (count, value) 连续段。"""
    runs: list[tuple[int, int]] = []
    for value in values:
        if runs and runs[-1][1] == value:
            runs[-1] = (runs[-1][0] + 1, value)
        else:
            runs.append((1, value))
    return runs


def _build_stbl(track: _Track, chunk_offsets: list[int], use_co64: bool) -> bytes:
    samples = [s for chunk in track.chunks for s in chunk["samples"]]
    if not samples:
        raise MuxError("没有采到任何样本，无法合成")

    durations = [s["duration"] for s in samples]
    sizes = [s["size"] for s in samples]
    cts = [s["cts"] for s in samples]
    sync: list[int] = []
    for index, sample in enumerate(samples, start=1):
        if not sample["flags"] & _SAMPLE_IS_NON_SYNC:
            sync.append(index)

    stsd = track.stsd
    stts_runs = _compress_runs(durations)
    stts = _full_box(b"stts", 0, 0, struct.pack(">I", len(stts_runs)),
                     b"".join(struct.pack(">II", c, d) for c, d in stts_runs))

    boxes = [stsd, stts]

    # composition offset：全 0 就不写，有负值用 version 1（有符号）
    if any(cts):
        negative = any(value < 0 for value in cts)
        ctts_runs = _compress_runs(cts)
        payload = struct.pack(">I", len(ctts_runs))
        for count, value in ctts_runs:
            payload += struct.pack(">Ii" if negative else ">II", count, value)
        boxes.append(_full_box(b"ctts", 1 if negative else 0, 0, payload))

    # 只有非关键帧时才需要 stss（全关键帧可省）
    if track.is_video and sync and len(sync) != len(samples):
        boxes.append(_full_box(b"stss", 0, 0, struct.pack(">I", len(sync)),
                               b"".join(struct.pack(">I", i) for i in sync)))

    # stsc：一个分片一「块」，块内样本数可能不同，连续相同就合并
    entries: list[tuple[int, int, int]] = []
    chunk_first = 1
    for chunk in track.chunks:
        count = len(chunk["samples"])
        if entries and entries[-1][1] == count:
            pass
        else:
            entries.append((chunk_first, count, 1))
        chunk_first += 1
    stsc_payload = struct.pack(">I", len(entries)) + b"".join(
        struct.pack(">III", first, count, sdi) for first, count, sdi in entries
    )
    boxes.append(_full_box(b"stsc", 0, 0, stsc_payload))

    boxes.append(_full_box(b"stsz", 0, 0, struct.pack(">II", 0, len(sizes)),
                           b"".join(struct.pack(">I", s) for s in sizes)))

    if use_co64:
        boxes.append(_full_box(b"co64", 0, 0, struct.pack(">I", len(chunk_offsets)),
                               b"".join(struct.pack(">Q", o) for o in chunk_offsets)))
    else:
        boxes.append(_full_box(b"stco", 0, 0, struct.pack(">I", len(chunk_offsets)),
                               b"".join(struct.pack(">I", o) for o in chunk_offsets)))

    return _box(b"stbl", *boxes)


def _build_trak(track: _Track, chunk_offsets: list[int], movie_timescale: int,
                use_co64: bool) -> bytes:
    duration_movie = int(round(track.duration / track.timescale * movie_timescale))
    # tkhd v0：creation/modification/track_id/reserved/duration → 保留 8 →
    # layer/alt_group/volume/reserved → matrix 36 → width/height（16.16 定点）
    tkhd = _full_box(
        b"tkhd", 0, 3,
        struct.pack(">IIIII", 0, 0, track.movie_id, 0, duration_movie),
        b"\x00" * 8,                      # reserved
        struct.pack(">hhhH", 0, 0, 0, 0),  # layer / alternate_group / volume / reserved
        struct.pack(">9i", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000),
        struct.pack(">II", (track.width << 16) if track.is_video else 0,
                    (track.height << 16) if track.is_video else 0),
    )

    mdhd = _full_box(b"mdhd", 0, 0,
                     struct.pack(">IIIIHH", 0, 0, track.timescale,
                                 track.duration, 0x55C4, 0))
    hdlr = _full_box(b"hdlr", 0, 0, b"\x00" * 4, track.kind, b"\x00" * 12,
                     b"Huzhmi\x00")
    if track.is_video:
        header_box = _full_box(b"vmhd", 0, 1, struct.pack(">HHHH", 0, 0, 0, 0))
    else:
        header_box = _full_box(b"smhd", 0, 0, struct.pack(">hH", 0, 0))
    dinf = _box(b"dinf", _full_box(b"dref", 0, 0, struct.pack(">I", 1),
                                   _full_box(b"url ", 0, 1)))
    stbl = _build_stbl(track, chunk_offsets, use_co64)
    minf = _box(b"minf", header_box, dinf, stbl)
    mdia = _box(b"mdia", mdhd, hdlr, minf)
    parts = [tkhd]
    if track.edit_media_time is not None:
        parts.append(_build_edts(track, duration_movie))
    parts.append(mdia)
    return _box(b"trak", *parts)


def _build_moov(order: list[_Track], offsets: list[list[int]],
                movie_timescale: int, use_co64: bool) -> bytes:
    duration = 0
    for track in order:
        duration = max(duration,
                       int(round(track.duration / track.timescale * movie_timescale)))
    mvhd = _full_box(b"mvhd", 0, 0,
                     struct.pack(">IIII", 0, 0, movie_timescale, duration),
                     struct.pack(">iH", 0x10000, 0x100), b"\x00" * 10,
                     struct.pack(">9i", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0,
                                 0x40000000),
                     b"\x00" * 24, struct.pack(">I", len(order) + 1))
    traks = [_build_trak(track, offsets[index], movie_timescale, use_co64)
             for index, track in enumerate(order)]
    return _box(b"moov", mvhd, *traks)


def _ftyp(has_hevc: bool = False) -> bytes:
    brands = b"isom" + struct.pack(">I", 512) + b"isomiso2mp41"
    if has_hevc:
        brands += b"hvc1"
    return _box(b"ftyp", brands)


def _build_edts(track: _Track, duration_movie: int) -> bytes:
    """按源 media_time 生成一个时长正确的 elst。

    源文件的 `segment_duration` 写的是 0（"到结尾"），且按源 movie timescale
    表达；这里换成我们自己的时长，其余保持原样。
    """
    media_time = track.edit_media_time or 0
    entry = struct.pack(">Iii", duration_movie, media_time, 0x00010000)
    return _box(b"edts", _full_box(b"elst", 0, 0, struct.pack(">I", 1), entry))


def mux(video: str | Path, audio: str | Path | None, out: str | Path,
        progress=None) -> dict:
    """把 video/audio 两路 m4s 合成 out.mp4。返回统计信息。

    `progress` 接受一个 `fn(fraction: float)`，0~1。
    """
    video_path = Path(video)
    audio_path = Path(audio) if audio else None
    out_path = Path(out)

    order: list[_Track] = []
    sources: dict[str, Path] = {}
    with open(video_path, "rb") as fh:
        tracks, movie_timescale = _parse_init(fh)
        _parse_fragments(fh, tracks)
    video_tracks = [t for t in tracks.values() if t.is_video]
    if not video_tracks:
        raise MuxError("视频流里没有画面轨，可能不是 DASH 分片")
    order.append(video_tracks[0])
    sources["video"] = video_path

    if audio_path is not None:
        with open(audio_path, "rb") as fh:
            audio_tracks, _ = _parse_init(fh)
            _parse_fragments(fh, audio_tracks)
        audio_only = [t for t in audio_tracks.values() if not t.is_video]
        if audio_only:
            order.append(audio_only[0])
            sources["audio"] = audio_path

    return _write(order, sources, out_path, movie_timescale, progress)


def extract_audio(src: str | Path, out: str | Path, progress=None) -> dict:
    """把 MP4 里的音轨单独装成一个 .m4a（不重编码，音质零损失）。

    和 `mux` 用的是同一套写文件逻辑，只是只装一条轨：mp4 的 moov/mdat
    偏移全部重算，所以出来的文件是标准的单轨 MP4（AAC → .m4a）。
    """
    src_path = Path(src)
    out_path = Path(out)
    with open(src_path, "rb") as fh:
        tracks, movie_timescale = _parse_init(fh)
        _parse_fragments(fh, tracks)
    audio_tracks = [t for t in tracks.values() if not t.is_video]
    if not audio_tracks:
        raise MuxError("这个文件里没有音轨，提取不了音频")
    audio = max(audio_tracks, key=lambda t: t.samples)
    return _write([audio], {"audio": src_path}, out_path, movie_timescale, progress)


def _write(order: list[_Track], sources: dict[str, Path], out_path: Path,
           movie_timescale: int, progress=None) -> dict:
    """把排好序的轨道写成一个普通（非分片）MP4。"""
    if len(order) == 1 and movie_timescale <= 0:
        movie_timescale = order[0].timescale
    if not sum(t.samples for t in order):
        raise MuxError("没有采到任何样本，无法合成")
    for index, track in enumerate(order, start=1):
        track.movie_id = index

    # 交错：按分片起始时间排序，同一时刻先视频后音频
    segments: list[tuple[float, int, _Track, dict]] = []
    for rank, track in enumerate(order):
        for chunk in track.chunks:
            segments.append((chunk["start"], rank, track, chunk))
    segments.sort(key=lambda item: (item[0], item[1]))

    total_payload = sum(
        sample["size"] for _, _, _, chunk in segments for sample in chunk["samples"]
    )
    use_co64 = total_payload > 0xFFFFFFFF
    has_hevc = any(b"hvc1" in t.stsd or b"hev1" in t.stsd for t in order)

    ftyp = _ftyp(has_hevc)
    # 按 order 里的位置索引：视频/音频是两个文件，track_id 都可能是 1，不能拿它当键
    offsets: list[list[int]] = [[] for _ in order]
    # 先用 0 占位量出 moov 长度（stco 条目数必须与真实一致，否则长度会变）
    placeholder = [[0] * len(track.chunks) for track in order]
    moov_len = len(_build_moov(order, placeholder, movie_timescale, use_co64))
    mdat_start = len(ftyp) + moov_len + 8

    rank_of = {id(track): i for i, track in enumerate(order)}
    cursor = mdat_start
    for _, _, track, chunk in segments:
        offsets[rank_of[id(track)]].append(cursor)
        cursor += sum(sample["size"] for sample in chunk["samples"])
    moov = _build_moov(order, offsets, movie_timescale, use_co64)
    if len(moov) != moov_len:
        # 偏移量本身长度固定；这里只防御性地确认一次
        raise MuxError("内部错误：moov 长度不稳定")

    written = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".part")
    readers = {role: open(path, "rb") for role, path in sources.items()}
    try:
        with open(tmp, "wb") as dst:
            dst.write(ftyp)
            dst.write(moov)
            dst.write(struct.pack(">I", total_payload + 8) + b"mdat")
            for _, _, track, chunk in segments:
                fh = readers["video" if track.is_video else "audio"]
                for sample in chunk["samples"]:
                    fh.seek(sample["offset"])
                    remaining = sample["size"]
                    while remaining:
                        data = fh.read(min(remaining, 1 << 20))
                        if not data:
                            raise MuxError("分片数据不完整（样本被截断）")
                        dst.write(data)
                        remaining -= len(data)
                written += sum(sample["size"] for sample in chunk["samples"])
                if progress is not None and total_payload:
                    progress(min(1.0, written / total_payload))
        tmp.replace(out_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        for fh in readers.values():
            fh.close()

    duration = max(
        (t.duration / t.timescale for t in order if t.timescale), default=0.0
    )
    return {
        "duration": round(duration, 3),
        "size": out_path.stat().st_size,
        "video_samples": order[0].samples if order[0].is_video else 0,
        "audio_samples": sum(t.samples for t in order if not t.is_video),
        "has_audio": any(not t.is_video for t in order),
    }


def probe(path: str | Path) -> dict:
    """读回自己写出来的 MP4，检查 box / 样本数 / 时长。"""
    path = Path(path)
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        total = fh.tell()
        tops = [typ.decode("latin1") for typ, _, _, _ in _iter_boxes(fh, 0, total)]
        fh.seek(0)
        head = fh.read(64)
        tracks, movie_timescale = _parse_init(fh)
        _parse_fragments(fh, tracks)
        info = {
            "path": str(path),
            "bytes": total,
            "boxes": tops,
            "movie_timescale": movie_timescale,
            "major_brand": head[8:12].decode("latin1") if len(head) >= 12 else "",
            "tracks": [
                {
                    "id": t.track_id,
                    "kind": t.kind.decode("latin1"),
                    "timescale": t.timescale,
                    "samples": t.samples,
                    "duration": round(t.duration / t.timescale, 3) if t.timescale else 0,
                    "width": t.width,
                    "height": t.height,
                    "has_edts": t.edit_media_time is not None,
                }
                for t in tracks.values()
            ],
        }
    return info
