"""音频文件体检：判断「是不是真的能播」，而不是只看后缀。

之所以要这层：音乐下载最常见的坏法不是下载失败，而是**把平台返回的错误页
（HTML/JSON）原样写成 .mp3**——文件有几百 KB、后缀也对，播放器一打开就报错。
这层用容器/帧头解析 + 时长估算来识别这类文件，纯 Python，不依赖 ffmpeg。
"""

from __future__ import annotations

import json
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

_HTML_MARKS = (b"<!doctype", b"<html", b"<?xml", b"<head", b"{\"", b"[{")
_AUDIO_KINDS = ("mp3", "wav", "flac", "m4a", "ogg", "aiff", "wma")
_MIN_BYTES = 8 * 1024      # 小于 8 KB 的“音乐”基本可以判死
_MIN_SECONDS = 1.0
_ASF_MAGIC = b"\x30\x26\xb2\x75\x8e\x66\xcf\x11\xa6\xd9\x00\xaa\x00\x62\xce\x6c"


@dataclass
class AudioInfo:
    path: str
    size: int
    kind: str            # mp3 / wav / flac / m4a / ogg / html / empty / unknown
    ok: bool
    reason: str
    duration: float = 0.0
    sample_rate: int = 0
    channels: int = 0
    bitrate: int = 0     # kbps

    def as_dict(self) -> dict:
        return asdict(self)


def _mp3_frame(head: bytes, offset: int = 0) -> tuple[int, int, int] | None:
    """解析一帧 MP3 头：返回 (bitrate kbps, sample_rate, channels)。"""
    if len(head) < offset + 4:
        return None
    b1, b2, b3 = head[offset + 1], head[offset + 2], head[offset + 3]
    if head[offset] != 0xFF or (b1 & 0xE0) != 0xE0:
        return None
    version = (b1 >> 3) & 0x03        # 3=MPEG1, 2=MPEG2, 0=MPEG2.5
    layer = (b1 >> 1) & 0x03          # 1=LayerIII
    if version == 1 or layer == 0:
        return None
    bitrate_index = (b2 >> 4) & 0x0F
    rate_index = (b2 >> 2) & 0x03
    padding = (b2 >> 1) & 0x01
    if bitrate_index in (0, 15) or rate_index == 3:
        return None

    tables = {
        3: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
        2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    }
    table = tables[3 if version == 3 else 2]
    bitrate = table[bitrate_index]
    base_rates = (44100, 48000, 32000)
    sample_rate = base_rates[rate_index]
    if version == 2:
        sample_rate //= 2
    elif version == 0:
        sample_rate //= 4
    channels = 1 if ((b3 >> 6) & 0x03) == 3 else 2
    _ = padding
    return bitrate, sample_rate, channels


def _id3_size(head: bytes) -> int:
    if head[:3] != b"ID3" or len(head) < 10:
        return 0
    size = ((head[6] & 0x7F) << 21) | ((head[7] & 0x7F) << 14) | \
           ((head[8] & 0x7F) << 7) | (head[9] & 0x7F)
    return size + 10


def _sniff_mp3(path: Path, size: int) -> AudioInfo:
    with path.open("rb") as fh:
        header = fh.read(10)
        skip = _id3_size(header)
        fh.seek(skip)          # 没有 ID3 时也要回到 0，否则会从第 10 字节开始找帧头
        head = fh.read(64 * 1024)
    frame = _mp3_frame(head, 0)
    offset = 0
    if frame is None:
        # ID3 之后可能还有填充，往前找几 KB
        for offset in range(0, min(len(head) - 4, 4096)):
            frame = _mp3_frame(head, offset)
            if frame:
                break
    if frame is None:
        return AudioInfo(str(path), size, "mp3", False,
                         "找不到 MP3 帧头（文件可能被截断或是别的内容）")
    bitrate, sample_rate, channels = frame
    duration = (size - skip - offset) * 8 / (bitrate * 1000) if bitrate else 0.0
    return AudioInfo(str(path), size, "mp3", True, "", round(duration, 2),
                     sample_rate, channels, bitrate)


def _sniff_wav(path: Path, size: int) -> AudioInfo:
    with path.open("rb") as fh:
        data = fh.read(min(size, 4096))
        fh.seek(0)
        header = fh.read(64)
    if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        return AudioInfo(str(path), size, "wav", False, "不是合法的 WAV 容器")
    rate = channels = bits = 0
    data_size = 0
    with path.open("rb") as fh:
        fh.seek(12)
        while True:
            chunk = fh.read(8)
            if len(chunk) < 8:
                break
            name, length = chunk[:4], struct.unpack("<I", chunk[4:])[0]
            if name == b"fmt ":
                body = fh.read(length)
                if len(body) >= 16:
                    channels = struct.unpack("<H", body[2:4])[0]
                    rate = struct.unpack("<I", body[4:8])[0]
                    bits = struct.unpack("<H", body[14:16])[0]
                else:
                    fh.seek(length, 1)
            elif name == b"data":
                data_size = length
                fh.seek(length, 1)
            else:
                fh.seek(length, 1)
            if fh.tell() % 2:
                fh.seek(1, 1)
    if not rate or not channels or not bits:
        return AudioInfo(str(path), size, "wav", False, "WAV 缺少 fmt 块")
    if data_size and data_size > size:
        return AudioInfo(str(path), size, "wav", False,
                         f"WAV 数据段声明的长度（{data_size}）大于文件本身，被截断了")
    duration = data_size / (rate * channels * bits / 8) if data_size else 0.0
    if duration <= 0:
        return AudioInfo(str(path), size, "wav", False, "WAV 里没有音频数据")
    return AudioInfo(str(path), size, "wav", True, "", round(duration, 2),
                     rate, channels, 0)


def _sniff_flac(path: Path, size: int) -> AudioInfo:
    with path.open("rb") as fh:
        fh.seek(4)
        block = fh.read(4)
        if len(block) < 4:
            return AudioInfo(str(path), size, "flac", False, "FLAC 头部不完整")
        info = fh.read(34)
    if len(info) < 34:
        return AudioInfo(str(path), size, "flac", False, "FLAC STREAMINFO 缺失")
    packed = int.from_bytes(info[10:18], "big")
    sample_rate = (packed >> 44) & 0xFFFFF
    channels = ((packed >> 41) & 0x07) + 1
    total = packed & 0xFFFFFFFFF
    duration = total / sample_rate if sample_rate else 0.0
    return AudioInfo(str(path), size, "flac", True, "", round(duration, 2),
                     sample_rate, channels, 0)


def _sniff_mp4(path: Path, size: int) -> AudioInfo:
    try:
        from .. import mp4mux

        info = mp4mux.probe(path)
    except Exception as exc:  # noqa: BLE001 - 探不动就当坏的
        return AudioInfo(str(path), size, "m4a", False, f"MP4 结构解析失败：{exc}")
    audio = [t for t in info.get("tracks") or [] if t.get("kind") == "soun"]
    if not audio:
        return AudioInfo(str(path), size, "m4a", False, "MP4 里没有音轨")
    track = max(audio, key=lambda t: t.get("samples") or 0)
    return AudioInfo(str(path), size, "m4a", True, "",
                     float(track.get("duration") or 0), track.get("timescale") or 0,
                     0, 0)


def _extended_rate(raw: bytes) -> float:
    """AIFF 的 80 位 extended 采样率。"""
    if len(raw) < 10:
        return 0.0
    exponent = ((raw[0] & 0x7F) << 8) | raw[1]
    mantissa = int.from_bytes(raw[2:10], "big")
    if not exponent and not mantissa:
        return 0.0
    value = mantissa * (2.0 ** (exponent - 16383 - 63))
    return -value if raw[0] & 0x80 else value


def _sniff_aiff(path: Path, size: int) -> AudioInfo:
    with path.open("rb") as fh:
        head = fh.read(4096)
    if head[:4] != b"FORM" or head[8:12] not in (b"AIFF", b"AIFC"):
        return AudioInfo(str(path), size, "aiff", False, "不是合法的 AIFF 容器")
    pos = 12
    while pos + 8 <= len(head):
        name = head[pos:pos + 4]
        length = int.from_bytes(head[pos + 4:pos + 8], "big")
        body = head[pos + 8:pos + 8 + length]
        if name == b"COMM" and len(body) >= 18:
            channels = int.from_bytes(body[0:2], "big")
            frames = int.from_bytes(body[2:6], "big")
            bits = int.from_bytes(body[6:8], "big")
            rate = _extended_rate(body[8:18])
            duration = frames / rate if rate else 0.0
            return AudioInfo(str(path), size, "aiff", True, "",
                             round(duration, 2), int(rate), channels,
                             int(rate * channels * bits / 1000) if rate else 0)
        if length <= 0:
            break
        pos += 8 + length + (length & 1)
    return AudioInfo(str(path), size, "aiff", False, "AIFF 缺少 COMM 块")


def _sniff_asf(path: Path, size: int) -> AudioInfo:
    with path.open("rb") as fh:
        head = fh.read(8192)
    if head[:16] != _ASF_MAGIC:
        return AudioInfo(str(path), size, "wma", False, "不是合法的 ASF/WMA 容器")
    file_props = bytes.fromhex("A1DCAB8C47A9CF118EE400C00C205365")
    duration = 0.0
    pos = 30
    while pos + 24 <= len(head):
        guid = head[pos:pos + 16]
        length = int.from_bytes(head[pos + 16:pos + 24], "little")
        if length < 24:
            break
        if guid == file_props:
            body = head[pos + 24:pos + length]
            if len(body) >= 48:
                play = int.from_bytes(body[40:48], "little")
                preroll = int.from_bytes(body[56:64], "little") if len(body) >= 64 else 0
                duration = max(0.0, play / 10_000_000 - preroll / 1000)
            break
        pos += length
    return AudioInfo(str(path), size, "wma", True, "", round(duration, 2),
                     0, 0, 0)


def sniff(path: str | Path) -> AudioInfo:
    """体检单个文件。"""
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        return AudioInfo(str(path), 0, "unknown", False, f"读不到文件：{exc}")
    if size == 0:
        return AudioInfo(str(path), 0, "empty", False, "空文件（0 字节）")

    with path.open("rb") as fh:
        head = fh.read(512)
    low = head.lstrip()[:8].lower()
    if any(low.startswith(mark[:8]) for mark in _HTML_MARKS):
        return AudioInfo(str(path), size, "html", False,
                         "内容是网页/JSON（平台错误页被存成了音频），不是音乐")

    suffix = path.suffix.lower()
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return _sniff_wav(path, size)
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return _sniff_aiff(path, size)
    if head[:16] == _ASF_MAGIC:
        return _sniff_asf(path, size)
    if head[:4] == b"fLaC":
        return _sniff_flac(path, size)
    if head[:4] == b"OggS":
        return AudioInfo(str(path), size, "ogg", True, "", 0, 0, 0, 0)
    if head[4:8] == b"ftyp":
        return _sniff_mp4(path, size)
    if head[:3] == b"ID3" or (head[:1] == b"\xff" and (head[1] & 0xE0) == 0xE0):
        return _sniff_mp3(path, size)
    if suffix == ".mp3":
        return AudioInfo(str(path), size, "mp3", False, "后缀是 mp3 但没有 MP3 头")

    return AudioInfo(str(path), size, "unknown", False,
                     f"认不出的格式（开头 {head[:8]!r}）")


def judge(info: AudioInfo) -> AudioInfo:
    """在头部解析之上补一层「能不能听」的判断。"""
    if not info.ok:
        return info
    if info.size < _MIN_BYTES:
        info.ok = False
        info.reason = f"文件只有 {info.size} 字节，不像完整歌曲"
    elif info.duration and info.duration < _MIN_SECONDS:
        info.ok = False
        info.reason = f"时长只有 {info.duration:.2f} 秒，基本是截断/试听片段"
    elif not info.duration:
        info.reason = "头部正常（时长无法估算）"
    return info


def check(path: str | Path) -> AudioInfo:
    return judge(sniff(path))


def scan(root: str | Path, suffixes: tuple[str, ...] = (".mp3", ".wav", ".flac",
                                                       ".m4a", ".ogg", ".aac")) -> dict:
    """扫一个目录，返回体检报告。"""
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"目录不存在：{root}")

    files = [p for p in root.rglob("*")
             if p.is_file() and p.suffix.lower() in suffixes]
    items = [check(p) for p in files]
    bad = [item for item in items if not item.ok]
    total_seconds = sum(item.duration for item in items if item.ok)
    return {
        "root": str(root),
        "total": len(items),
        "ok": len(items) - len(bad),
        "broken": len(bad),
        "seconds": round(total_seconds, 1),
        "kinds": _count_kinds(items),
        "bad_files": [item.as_dict() for item in bad[:500]],
    }


def _count_kinds(items: list[AudioInfo]) -> dict:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.kind] = counts.get(item.kind, 0) + 1
    return counts


def report(scan_result: dict) -> str:
    """把报告排版成能直接贴给人看的文本。"""
    lines = [
        f"目录：{scan_result['root']}",
        f"音频文件 {scan_result['total']} 个：正常 {scan_result['ok']}，"
        f"有问题 {scan_result['broken']}",
        "格式分布：" + "，".join(
            f"{kind} {count}" for kind, count in scan_result["kinds"].items()),
    ]
    for item in scan_result["bad_files"]:
        lines.append(f"  ✗ {Path(item['path']).name}  [{item['kind']}] {item['reason']}")
    return "\n".join(lines)


if __name__ == "__main__":  # python -m huzhmi.media.verify <目录>
    import sys

    result = scan(sys.argv[1] if len(sys.argv) > 1 else ".")
    print(report(result))
    print(json.dumps(result["kinds"], ensure_ascii=False))
