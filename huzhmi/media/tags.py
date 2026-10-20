"""读音频标签（ID3v2 / ID3v1 / FLAC / MP4），用来按专辑归档。

纯 Python，不引入 mutagen 之类的依赖——只需要标题/艺术家/专辑/轨号四个字段，
自己解析比装库更省事，也方便处理脏标签（乱码、缺字段）。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

_TEXT_ENCODINGS = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}


@dataclass
class Tags:
    title: str = ""
    artist: str = ""
    album: str = ""
    track: int = 0
    year: str = ""

    def merged(self, fallback_stem: str) -> "Tags":
        if not self.title:
            self.title = fallback_stem
        return self


def _decode_text(raw: bytes, encoding: int) -> str:
    codec = _TEXT_ENCODINGS.get(encoding, "utf-8")
    try:
        text = raw.decode(codec, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    return text.replace("\x00", "").strip()


def _id3_text(frame: bytes) -> str:
    if not frame:
        return ""
    return _decode_text(frame[1:], frame[0])


def _syncsafe(value: bytes) -> int:
    return ((value[0] & 0x7F) << 21) | ((value[1] & 0x7F) << 14) | \
           ((value[2] & 0x7F) << 7) | (value[3] & 0x7F)


def read_id3v2(head: bytes) -> Tags:
    tags = Tags()
    if head[:3] != b"ID3" or len(head) < 10:
        return tags
    version = head[3]
    size = _syncsafe(head[6:10])
    body = head[10:10 + size]
    pos = 0
    while pos + 10 <= len(body):
        frame_id = body[pos:pos + 4]
        if not frame_id.strip(b"\x00"):
            break
        if version >= 4:
            frame_size = _syncsafe(body[pos + 4:pos + 8])
        else:
            frame_size = struct.unpack(">I", body[pos + 4:pos + 8])[0]
        if frame_size <= 0 or pos + 10 + frame_size > len(body):
            break
        payload = body[pos + 10:pos + 10 + frame_size]
        if frame_id == b"TIT2":
            tags.title = _id3_text(payload)
        elif frame_id == b"TPE1":
            tags.artist = _id3_text(payload)
        elif frame_id == b"TALB":
            tags.album = _id3_text(payload)
        elif frame_id in (b"TRCK", b"TRCK"):
            raw = _id3_text(payload)
            tags.track = int(raw.split("/")[0]) if raw.split("/")[0].isdigit() else 0
        elif frame_id in (b"TYER", b"TDRC"):
            tags.year = _id3_text(payload)[:4]
        pos += 10 + frame_size
    return tags


def read_id3v1(tail: bytes) -> Tags:
    tags = Tags()
    if len(tail) < 128 or tail[-128:-125] != b"TAG":
        return tags
    block = tail[-128:]
    for offset, name in ((3, "title"), (33, "artist"), (63, "album")):
        raw = block[offset:offset + 30].split(b"\x00")[0]
        value = raw.decode("latin-1", errors="replace").strip()
        if name == "title":
            tags.title = value
        elif name == "artist":
            tags.artist = value
        else:
            tags.album = value
    year = block[93:97].decode("latin-1", errors="replace").strip()
    tags.year = year
    return tags


def read_flac(data: bytes) -> Tags:
    tags = Tags()
    if data[:4] != b"fLaC":
        return tags
    pos = 4
    while pos + 4 <= len(data):
        header = data[pos]
        last = header & 0x80
        block_type = header & 0x7F
        length = int.from_bytes(data[pos + 1:pos + 4], "big")
        body = data[pos + 4:pos + 4 + length]
        if block_type == 4:  # VORBIS_COMMENT
            try:
                vendor_len = int.from_bytes(body[:4], "little")
                cursor = 4 + vendor_len
                count = int.from_bytes(body[cursor:cursor + 4], "little")
                cursor += 4
                for _ in range(count):
                    item_len = int.from_bytes(body[cursor:cursor + 4], "little")
                    cursor += 4
                    item = body[cursor:cursor + item_len].decode("utf-8", "replace")
                    cursor += item_len
                    if "=" not in item:
                        continue
                    key, value = item.split("=", 1)
                    key = key.upper()
                    if key == "TITLE":
                        tags.title = value
                    elif key == "ARTIST":
                        tags.artist = value
                    elif key == "ALBUM":
                        tags.album = value
                    elif key == "TRACKNUMBER":
                        head = value.split("/")[0]
                        tags.track = int(head) if head.isdigit() else 0
                    elif key == "DATE":
                        tags.year = value[:4]
            except Exception:  # noqa: BLE001 - 脏标签就当没有
                pass
            break
        pos += 4 + length
        if last:
            break
    return tags


def read_mp4(data: bytes) -> Tags:
    """从 MP4 的 ilst 里读 ©nam / ©ART / ©alb / trkn。"""
    tags = Tags()
    marker = data.find(b"ilst")
    if marker < 0:
        return tags
    end = len(data)
    pos = marker + 4
    while pos + 8 <= end:
        size = int.from_bytes(data[pos:pos + 4], "big")
        if size < 8 or pos + size > end:
            break
        name = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + size]
        text = ""
        if b"data" in body:
            payload = body.split(b"data", 1)[1][4:]
            text = payload[4:].replace(b"\x00", b"").decode("utf-8", "replace")
        if name == b"\xa9nam":
            tags.title = text
        elif name == b"\xa9ART":
            tags.artist = text
        elif name == b"\xa9alb":
            tags.album = text
        elif name == b"trkn":
            nums = [b for b in body if b]
            if len(nums) >= 2:
                tags.track = nums[-2]
        pos += size
    return tags


def read(path: str | Path) -> Tags:
    path = Path(path)
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            head = fh.read(512 * 1024)
            fh.seek(max(0, size - 128))
            tail = fh.read(128)
    except OSError:
        return Tags()

    suffix = path.suffix.lower()
    tags = Tags()
    if head[:3] == b"ID3":
        tags = read_id3v2(head)
    if not (tags.title or tags.artist or tags.album) and head[:4] == b"fLaC":
        tags = read_flac(head)
    if not (tags.title or tags.artist or tags.album) and head[4:8] == b"ftyp":
        tags = read_mp4(head)
    if not (tags.title or tags.artist or tags.album):
        tags = read_id3v1(tail)
    if not tags.title:
        tags.title = path.stem
    return tags
