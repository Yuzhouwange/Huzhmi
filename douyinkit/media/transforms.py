"""「转换能力」注册表。

加一个新能力 = 写一个 `Transform` 再 `register()` 一下，engine / API / 界面
都不用改。这和平台适配器（`platforms.Platform`）是同一套思路。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import mp4mux
from . import ffmpeg


class TransformError(RuntimeError):
    """转换失败（参数不对、源文件不支持等）。"""


ToolMissing = ffmpeg.ToolMissing


@dataclass(frozen=True)
class Transform:
    name: str                       # audio_m4a / audio_mp3 / ...
    label: str                      # 界面上的中文名
    extension: str                  # 输出后缀，不含点：m4a / mp3
    kind: str = "audio"             # 产出类型
    needs: str | None = None        # None = 纯 Python；"ffmpeg" = 需要外部工具
    lossless: bool = False          # 是否无损（不重编码）
    codec_args: tuple[str, ...] = ()  # 给 ffmpeg 的编码参数
    note: str = ""

    def run(self, src: Path, dst: Path, on_progress: Callable[[float], None] | None = None,
            cfg: dict | None = None) -> dict:
        src, dst = Path(src), Path(dst)
        if not src.is_file():
            raise TransformError(f"源文件不存在：{src}")

        if self.needs == "ffmpeg":
            duration = 0.0
            try:
                duration = float(mp4mux.probe(src).get("tracks", [{}])[0]
                                 .get("duration") or 0)
            except Exception:  # noqa: BLE001 - 探不到就退回 ffprobe
                duration = ffmpeg.probe_duration(src, cfg)
            ffmpeg.run_audio(src, dst, list(self.codec_args), duration,
                             on_progress, cfg)
            return {"path": str(dst), "size": dst.stat().st_size,
                    "engine": "ffmpeg", "lossless": False}

        # 纯 Python：只搬运音轨，不重编码
        if on_progress:
            on_progress(0.05)
        stats = mp4mux.extract_audio(src, dst, progress=on_progress)
        return {"path": str(dst), "size": stats["size"], "engine": "python",
                "duration": stats["duration"], "samples": stats["audio_samples"],
                "lossless": True}


_TRANSFORMS: dict[str, Transform] = {}


def register(transform: Transform) -> Transform:
    _TRANSFORMS[transform.name] = transform
    return transform


def get(name: str) -> Transform:
    try:
        return _TRANSFORMS[name]
    except KeyError:
        raise TransformError(f"未知的转换类型：{name}") from None


def names() -> list[str]:
    return list(_TRANSFORMS)


def describe(cfg: dict | None = None) -> list[dict]:
    """给界面用：哪些能做、为什么不能做。"""
    has_ffmpeg = ffmpeg.available(cfg)
    rows = []
    for transform in _TRANSFORMS.values():
        ok = transform.needs is None or has_ffmpeg
        rows.append(
            {
                "name": transform.name,
                "label": transform.label,
                "extension": transform.extension,
                "kind": transform.kind,
                "lossless": transform.lossless,
                "needs": transform.needs,
                "available": ok,
                "reason": "" if ok else "需要 ffmpeg：请在设置里填 ffmpeg.exe 路径",
                "note": transform.note,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 内置能力
# ---------------------------------------------------------------------------

register(
    Transform(
        name="audio_m4a",
        label="提取音轨（无损 m4a）",
        extension="m4a",
        lossless=True,
        note="不重编码，音质和原视频完全一致；纯 Python 实现，不需要 ffmpeg",
    )
)

register(
    Transform(
        name="audio_mp3",
        label="转 MP3 320k",
        extension="mp3",
        needs="ffmpeg",
        codec_args=("-c:a", "libmp3lame", "-b:a", "320k", "-id3v2_version", "3"),
    )
)

register(
    Transform(
        name="audio_mp3_192",
        label="转 MP3 192k",
        extension="mp3",
        needs="ffmpeg",
        codec_args=("-c:a", "libmp3lame", "-b:a", "192k", "-id3v2_version", "3"),
    )
)

register(
    Transform(
        name="audio_flac",
        label="转 FLAC（无损）",
        extension="flac",
        needs="ffmpeg",
        lossless=True,
        codec_args=("-c:a", "flac", "-compression_level", "5"),
    )
)

register(
    Transform(
        name="audio_wav",
        label="转 WAV（未压缩）",
        extension="wav",
        needs="ffmpeg",
        lossless=True,
        codec_args=("-c:a", "pcm_s16le"),
    )
)
