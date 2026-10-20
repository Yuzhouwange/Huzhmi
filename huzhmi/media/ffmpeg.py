"""ffmpeg / ffprobe 的定位与调用。

只做三件事：**找工具、要进度、把参数拼对**。找不到就抛 `ToolMissing`，
让上层提示用户去设置里填路径——不自动下载，也不假装成功。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


class ToolMissing(RuntimeError):
    """没找到 ffmpeg。"""


class ToolError(RuntimeError):
    """ffmpeg 跑失败了。"""


def _exe(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def find_ffmpeg(cfg: dict | None = None) -> str | None:
    cfg = cfg or {}
    explicit = str(cfg.get("ffmpeg_path") or "").strip()
    if explicit:
        candidate = Path(explicit)
        if candidate.is_file():
            return str(candidate)
        found = shutil.which(explicit)
        if found:
            return found

    found = shutil.which("ffmpeg")
    if found:
        return found

    # 常见的「随程序带的工具」位置：数据目录、exe 同级、资源目录、LOCALAPPDATA
    candidates: list[Path] = []
    try:
        from ..paths import data_dir, resource_dir

        candidates.append(Path(data_dir()) / "tools" / _exe("ffmpeg"))
        candidates.append(Path(resource_dir()) / "tools" / _exe("ffmpeg"))
    except Exception:  # noqa: BLE001
        pass
    candidates.append(Path(sys.executable).resolve().parent / "tools" / _exe("ffmpeg"))
    candidates.append(Path(sys.executable).resolve().parent / _exe("ffmpeg"))
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(Path(local_appdata) / "Huzhmi" / "tools" / _exe("ffmpeg"))

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def available(cfg: dict | None = None) -> bool:
    return find_ffmpeg(cfg) is not None


def find_ffprobe(ffmpeg_path: str) -> str | None:
    """同名目录下找 ffprobe；没有就返回 None（时长可以从容器里读）。"""
    folder = Path(ffmpeg_path).parent
    candidate = folder / _exe("ffprobe")
    return str(candidate) if candidate.is_file() else None


def _no_window() -> int:
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def probe_duration(src: Path, cfg: dict | None = None) -> float:
    """尽量拿时长（秒）；拿不到返回 0，进度退化成不确定态。"""
    ffmpeg = find_ffmpeg(cfg)
    if not ffmpeg:
        return 0.0
    ffprobe = find_ffprobe(ffmpeg)
    try:
        if ffprobe:
            out = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", str(src)],
                capture_output=True, text=True, timeout=30,
                creationflags=_no_window(),
            )
            data = json.loads(out.stdout or "{}")
            return float((data.get("format") or {}).get("duration") or 0)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def run_audio(src: Path, dst: Path, codec_args: list[str], duration: float = 0.0,
              on_progress=None, cfg: dict | None = None,
              cover: Path | None = None, attached_pic: bool = False) -> None:
    """调用 ffmpeg 把 src 的音频转成 dst。`codec_args` 是编码参数。

    `cover` 给了就把图片作为封面写进去（MP3 用 ID3 APIC，FLAC 用 attached_pic）。
    """
    ffmpeg = find_ffmpeg(cfg)
    if not ffmpeg:
        raise ToolMissing(
            "需要 ffmpeg 才能转成这个格式。请在设置里填 ffmpeg.exe 的路径，"
            "或把 ffmpeg 加进 PATH"
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    # 临时文件必须保留真实后缀：ffmpeg 靠扩展名选封装格式，`.mp3.part` 会报
    # "Error opening output files: Invalid argument"
    tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
    ]
    if cover is not None:
        cmd += ["-i", str(cover), "-map", "0:a", "-map", "1:v", "-c:v", "copy"]
        if attached_pic:
            cmd += ["-disposition:v", "attached_pic"]
        cmd += ["-metadata:s:v", "title=Album cover",
                "-metadata:s:v", "comment=Cover (front)"]
    cmd += [
        "-vn", "-map_metadata", "0",
        *codec_args,
        "-progress", "pipe:1", "-nostats",
        str(tmp),
    ] if cover is None else [
        "-map_metadata", "0",
        *codec_args,
        "-progress", "pipe:1", "-nostats",
        str(tmp),
    ]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            creationflags=_no_window(),
        )
    except OSError as exc:
        raise ToolError(f"启动 ffmpeg 失败：{exc}") from exc

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if not line.startswith("out_time_ms=") or not on_progress:
                continue
            raw = line.split("=", 1)[1].strip()
            try:
                done = int(raw) / 1_000_000
            except ValueError:
                continue
            if duration > 0:
                on_progress(min(0.99, done / duration))
        stderr = proc.stderr.read() if proc.stderr else ""
        code = proc.wait()
    finally:
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()

    if code != 0:
        tmp.unlink(missing_ok=True)
        message = (stderr or "").strip().splitlines()
        raise ToolError(f"ffmpeg 转换失败：{message[-1] if message else code}")
    tmp.replace(dst)
    if on_progress:
        on_progress(1.0)


def retag(src: Path, dst: Path, metadata: list[tuple[str, str]],
          cover: Path | None = None, cfg: dict | None = None) -> None:
    """不重编码，只改标签 / 加封面（-c:a copy）。"""
    ffmpeg = find_ffmpeg(cfg)
    if not ffmpeg:
        raise ToolMissing("需要 ffmpeg 才能写标签 / 封面")
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src)]
    if cover is not None:
        cmd += ["-i", str(cover), "-map", "0:a", "-map", "1:v", "-c:v", "copy",
                "-disposition:v", "attached_pic",
                "-metadata:s:v", "title=Album cover",
                "-metadata:s:v", "comment=Cover (front)"]
    else:
        cmd += ["-map", "0:a"]
    cmd += ["-c:a", "copy", "-id3v2_version", "3", "-map_metadata", "0"]
    for key, value in metadata:
        if value:
            cmd += ["-metadata", f"{key}={value}"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.stem}.tag{dst.suffix}")
    cmd += [str(tmp)]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          creationflags=_no_window())
    if proc.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        message = (proc.stderr or "").strip().splitlines()
        raise ToolError(f"写标签失败：{message[-1] if message else proc.returncode}")
    tmp.replace(dst)
