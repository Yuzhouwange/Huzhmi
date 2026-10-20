"""NCM 解密（网易云音乐加密容器）。

等价于 https://github.com/magic-akari/ncmc 的 `ncmc -i in.ncm -o out`，
但用纯 Python 实现，不额外带 exe：

    CTENFDAM | 2B | key_len | key(^0x64) | meta_len | meta(^0x63)
    | crc 4B | 1B | cover_len | cover | audio

* 头部两把 AES-128-ECB 密钥解出 RC4 风格的 keybox 和歌曲元数据；
* 音频体按 `keybox[(keybox[j] + keybox[(keybox[j]+j)&0xff]) & 0xff]` 逐字节异或，
  j = (i + 1) & 0xff —— 只与 `i % 256` 有关，所以按 1 MB 分块流式解密，
  块大小是 256 的整数倍，边界不会错位；
* 解密后按魔数确认真实格式（mp3 / flac / m4a / wav），不信元数据里的 format。

找不到/解不开就抛 `NcmError`，错误信息带文件路径，不会留半截文件。
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import aes

MAGIC = b"CTENFDAM"
CORE_KEY = bytes.fromhex("687A4852416D736F356B496E62617857")
META_KEY = bytes.fromhex("2331346C6A6B5F215C5D2630553C2728")
KEY_PREFIX = b"neteasecloudmusic"
META_PREFIX = b"163 key(Don't modify):"
_CHUNK = 1024 * 1024
_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]+')


class NcmError(RuntimeError):
    """不是 NCM / 文件损坏 / 版本不认识。"""


@dataclass
class NcmMeta:
    title: str = ""
    artist: str = ""
    album: str = ""
    track: int = 0
    format: str = ""
    bitrate: int = 0
    duration: float = 0.0
    cover: bytes = b""

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "track": self.track,
            "format": self.format,
            "bitrate": self.bitrate,
            "duration": round(self.duration, 2),
            "cover": len(self.cover),
        }


def is_ncm(path: str | Path) -> bool:
    try:
        with Path(path).open("rb") as fh:
            return fh.read(8) == MAGIC
    except OSError:
        return False


def _safe(name: str, limit: int = 90, fallback: str = "未命名") -> str:
    text = _BAD_NAME.sub("_", str(name or "")).strip(" .")
    text = re.sub(r"\s+", " ", text)
    if not text:
        text = fallback
    return text[:limit].strip(" .") or fallback


def _build_keybox(key: bytes) -> list[int]:
    box = list(range(256))
    last = 0
    for index in range(256):
        swap = box[index]
        last = (swap + last + key[index % len(key)]) & 0xFF
        box[index] = box[last]
        box[last] = swap
    return box


def _decrypt_chunk(data: bytes, box: list[int]) -> bytes:
    out = bytearray(len(data))
    for index, byte in enumerate(data):
        cursor = (index + 1) & 0xFF
        out[index] = byte ^ box[(box[cursor] + box[(box[cursor] + cursor) & 0xFF]) & 0xFF]
    return bytes(out)


def _sniff_audio(head: bytes) -> str:
    if head[:3] == b"ID3":
        return "mp3"
    if head[:1] == b"\xff" and len(head) > 1 and (head[1] & 0xE0) == 0xE0:
        return "mp3"
    if head[:4] == b"fLaC":
        return "flac"
    if head[4:8] == b"ftyp":
        return "m4a"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"OggS":
        return "ogg"
    return ""


def _unpad(data: bytes) -> bytes:
    """去掉 PKCS#7 填充（有的 NCM 密钥块是整块加密的）。"""
    if not data:
        return data
    pad = data[-1]
    if 1 <= pad <= 16 and data.endswith(bytes([pad]) * pad):
        return data[:-pad]
    return data


def _unwrap_key(blob: bytes) -> bytes:
    """128 字节密钥块 → 真正的 keybox 种子。

    老版本把 `neteasecloudmusic` 明文写在加密块前面；新版本则把前缀一起
    加密进去，要先 AES 解密再剥前缀和填充。两种都认。
    """
    if blob.startswith(KEY_PREFIX):
        blob = blob[len(KEY_PREFIX):]
    if not blob or len(blob) % 16:
        raise NcmError("密钥块长度不是 16 的整数倍")
    plain = aes.decrypt_ecb(blob, CORE_KEY)
    if plain.startswith(KEY_PREFIX):
        plain = plain[len(KEY_PREFIX):]
    plain = _unpad(plain)
    if not plain:
        raise NcmError("密钥块解出来是空的")
    return plain


def _parse_meta(raw: bytes) -> NcmMeta:
    meta = NcmMeta()
    if not raw:
        return meta
    try:
        decoded = base64.b64decode(raw, validate=False)
    except (ValueError, TypeError) as exc:
        raise NcmError(f"元数据 base64 解码失败：{exc}") from exc
    if not decoded or len(decoded) % 16:
        return meta
    plain = aes.decrypt_ecb(decoded, META_KEY)
    if plain.startswith(b"music:"):
        plain = plain[6:]
    # JSON 后面跟着 PKCS#7 填充，直接按最后一个 } 截断最稳
    end = plain.rfind(b"}")
    if end >= 0:
        plain = plain[:end + 1]
    plain = plain.split(b"\x00", 1)[0]
    try:
        data = json.loads(plain.decode("utf-8", "replace"))
    except ValueError:
        return meta
    meta.title = str(data.get("musicName") or "")
    artists = data.get("artist") or []
    names: list[str] = []
    for item in artists:
        if isinstance(item, (list, tuple)) and item:
            names.append(str(item[0]))
        elif isinstance(item, str):
            names.append(item)
    meta.artist = " / ".join([name for name in names if name])
    meta.album = str(data.get("album") or "")
    meta.format = str(data.get("format") or "").lower()
    try:
        meta.bitrate = int(data.get("bitrate") or 0)
    except (TypeError, ValueError):
        meta.bitrate = 0
    try:
        meta.duration = float(data.get("duration") or 0) / 1000.0
    except (TypeError, ValueError):
        meta.duration = 0.0
    return meta


def read_meta(path: str | Path) -> NcmMeta:
    """只读头部，拿标题/歌手/专辑/封面（给批量列表用，不解音频）。"""
    path = Path(path)
    with path.open("rb") as fh:
        if fh.read(8) != MAGIC:
            raise NcmError(f"不是 NCM 文件：{path}")
        fh.read(2)
        key_len = int.from_bytes(fh.read(4), "little")
        fh.read(key_len)
        meta_len = int.from_bytes(fh.read(4), "little")
        raw = b""
        if meta_len:
            block = fh.read(meta_len)
            raw = bytes(byte ^ 0x63 for byte in block)
            if raw.startswith(META_PREFIX):
                raw = raw[len(META_PREFIX):]
        meta = _parse_meta(raw)
        fh.read(4)
        fh.read(1)
        image_space = int.from_bytes(fh.read(4), "little")
        image_size = int.from_bytes(fh.read(4), "little")
        if 0 < image_size <= 20 * 1024 * 1024 and image_size <= image_space:
            meta.cover = fh.read(image_size)
    return meta


def decode_file(src: str | Path, dst_dir: str | Path | None = None, *,
                keep_cover: bool = True,
                on_progress=None) -> dict:
    """解密一个 .ncm。

    返回 `{ok, path, meta, size}`；失败抛 `NcmError`。
    `dst_dir` 为空时写在源文件旁边。
    """
    src = Path(src)
    if not src.is_file():
        raise NcmError(f"文件不存在：{src}")
    dst_dir = Path(dst_dir) if dst_dir else src.parent
    dst_dir.mkdir(parents=True, exist_ok=True)

    with src.open("rb") as fh:
        if fh.read(8) != MAGIC:
            raise NcmError(f"不是 NCM 文件：{src.name}")
        fh.read(2)
        key_len = int.from_bytes(fh.read(4), "little")
        if key_len <= 0 or key_len > 1024:
            raise NcmError(f"密钥长度异常（{key_len}）：{src.name}")
        key_data = bytes(byte ^ 0x64 for byte in fh.read(key_len))
        if not key_data or len(key_data) % 16:
            raise NcmError(f"密钥块长度异常：{src.name}")
        key = _unwrap_key(key_data)

        meta_len = int.from_bytes(fh.read(4), "little")
        raw_meta = b""
        if meta_len:
            if meta_len > 1024 * 1024:
                raise NcmError(f"元数据长度异常：{src.name}")
            block = fh.read(meta_len)
            raw_meta = bytes(byte ^ 0x63 for byte in block)
            if raw_meta.startswith(META_PREFIX):
                raw_meta = raw_meta[len(META_PREFIX):]
        meta = _parse_meta(raw_meta)

        fh.read(4)      # crc32
        fh.read(1)      # gap
        image_space = int.from_bytes(fh.read(4), "little")
        image_size = int.from_bytes(fh.read(4), "little")
        cover = b""
        if 0 < image_size <= 20 * 1024 * 1024 and image_size <= image_space:
            cover = fh.read(image_size)
            fh.seek(image_space - image_size, 1)   # 封面区可能有对齐填充
        elif image_space:
            fh.seek(image_space, 1)
        meta.cover = cover

        box = _build_keybox(key)
        start = fh.tell()
        total = max(0, src.stat().st_size - start)
        # 只嗅一下魔数，然后把读指针退回去：keybox 的相位按 i % 256 走，
        # 先单独解 64 字节会把后续字节的相位错开 64，解出来全是噪声。
        probe = fh.read(min(64, total)) if total else b""
        ext = _sniff_audio(probe) or meta.format or "mp3"
        fh.seek(start)

        title = meta.title or src.stem
        artist = meta.artist
        stem = f"{artist} - {title}" if artist else title
        target = dst_dir / f"{_safe(stem, 120, src.stem)}.{ext}"
        temp = target.with_name(f"{target.name}.part")
        # 同名不同源时别覆盖
        if target.exists() and not is_ncm(target):
            index = 2
            while target.exists():
                target = dst_dir / f"{_safe(stem, 110, src.stem)} ({index}).{ext}"
                index += 1
            temp = target.with_name(f"{target.name}.part")

        done = 0
        try:
            with temp.open("wb") as out:
                while True:
                    block = fh.read(_CHUNK)
                    if not block:
                        break
                    out.write(_decrypt_chunk(block, box))
                    done += len(block)
                    if on_progress and total:
                        on_progress(min(0.99, done / total))
            if done <= 0:
                raise NcmError(f"音频体为空：{src.name}")
            temp.replace(target)
        except OSError as exc:
            temp.unlink(missing_ok=True)
            raise NcmError(f"写入失败：{exc}") from exc

    if keep_cover and cover:
        cover_path = dst_dir / "cover.jpg"
        if not cover_path.exists():
            try:
                cover_path.write_bytes(cover)
            except OSError:
                pass
    if on_progress:
        on_progress(1.0)
    return {
        "ok": True,
        "path": str(target),
        "size": target.stat().st_size,
        "meta": meta.as_dict(),
        "source": str(src),
    }


def decode_with_ncmc(exe: str | Path, src: str | Path,
                     dst_dir: str | Path | None = None) -> dict:
    """可选：调用外部 ncmc.exe（Rust 版）。

    仅在用户显式配置了 `ncmc_path` 且内置解码失败时兜底；成功返回
    `{ok, path}`，输出文件按 ncmc 的默认命名（同目录、同名换后缀）。
    """
    exe = Path(exe)
    src = Path(src)
    dst_dir = Path(dst_dir) if dst_dir else src.parent
    if not exe.is_file():
        raise NcmError(f"ncmc 不存在：{exe}")
    dst_dir.mkdir(parents=True, exist_ok=True)
    target = dst_dir / f"{src.stem}.mp3"
    proc = subprocess.run(
        [str(exe), "-i", str(src), "-o", str(target)],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0 or not target.is_file():
        raise NcmError((proc.stderr or proc.stdout or "ncmc 执行失败").strip()[:200])
    return {"ok": True, "path": str(target), "size": target.stat().st_size,
            "meta": {}, "source": str(src)}


__all__ = ["NcmError", "NcmMeta", "decode_file", "decode_with_ncmc", "is_ncm",
           "read_meta"]
