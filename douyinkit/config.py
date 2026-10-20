"""配置读写：data/config.json。"""

from __future__ import annotations

import json
from pathlib import Path

from .paths import data_dir, resource_dir

# 打包成 exe 后资源在 _MEIPASS、数据在可写目录，两者不再相同
BASE_DIR = resource_dir()
DATA_DIR = data_dir()
CONFIG_PATH = DATA_DIR / "config.json"
DB_PATH = DATA_DIR / "douyinkit.db"

DEFAULTS = {
    "output_dir": str(Path.home() / "Downloads" / "DouyinKit"),
    # 平台/作者 两层；作品直接以文件名落到作者目录，不再单开一层文件夹
    "folder_template": "{platform}/{author}",
    "file_template": "{title}",
    "concurrency": 3,
    "cookie": "",
    "bilibili_cookie": "",
    "bili_qn": 0,
    "proxy": "",
    "retries": 3,
    # 提取音频用哪个预设（media.transforms 里的 name）
    "audio_preset": "audio_m4a",
    # 留空 = 用 PATH 里的 ffmpeg；填了就用这个 exe
    "ffmpeg_path": "",
    # 实况图（Live Photo）存什么：video=只存 mp4 / both=图片+视频 / image=只存图片
    "live_photo": "video",
    # 关闭应用时自动清掉已完成 / 失败 / 已取消的任务记录（文件保留，暂停中的不动）。
    # 想保留历史就改成 false；数据库只存任务状态，不存媒体内容。
    "clear_history_on_exit": True,
    # 抖音分享页三条链路都失败时，自动改用浏览器页面桥再试一次
    "auto_fallback": True,
    # 规则热更新地址（JSON）：启动时超过 24 小时会自动拉一次
    "rules_url": "",
    # 音乐下载
    "music_dir": str(Path.home() / "Music" / "DouyinKit"),
    "music_format": "mp3",          # mp3 / wav / flac / original
    "music_concurrency": 3,
    "music_save_lyrics": True,
    # 可选：外部 ncmc.exe（内置纯 Python 解码，一般不用）
    "ncmc_path": "",
    # 各平台 Cookie（可选，填了能下 320k / 会员曲）
    "music_cookie_netease": "",
    "music_cookie_qq": "",
    "music_cookie_kugou": "",
    "music_cookie_kuwo": "",
}

# 老版本的默认模板会套三层（平台/作者/作品）。这些值没有用户意图，
# 读配置时直接换成新默认值，否则升级后还是继续套文件夹。
_LEGACY_FOLDER_TEMPLATES = (
    "{platform}/{author}/{title}",
    "{platform}/{kind}/{author}/{title}",
)


def load() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text("utf-8"))
            if isinstance(raw, dict):
                cfg.update({k: v for k, v in raw.items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass
    if cfg["folder_template"] in _LEGACY_FOLDER_TEMPLATES:
        cfg["folder_template"] = DEFAULTS["folder_template"]
    if not str(cfg.get("file_template") or "").strip():
        cfg["file_template"] = DEFAULTS["file_template"]
    # 类型收敛，避免前端传来奇怪的值
    try:
        cfg["concurrency"] = max(1, min(8, int(cfg["concurrency"])))
    except (TypeError, ValueError):
        cfg["concurrency"] = DEFAULTS["concurrency"]
    try:
        cfg["retries"] = max(0, min(10, int(cfg["retries"])))
    except (TypeError, ValueError):
        cfg["retries"] = DEFAULTS["retries"]
    try:
        cfg["music_concurrency"] = max(1, min(6, int(cfg["music_concurrency"])))
    except (TypeError, ValueError):
        cfg["music_concurrency"] = DEFAULTS["music_concurrency"]
    if cfg.get("music_format") not in ("mp3", "wav", "flac", "original"):
        cfg["music_format"] = DEFAULTS["music_format"]
    # B 站画质上限：0 = 最高可用；其他值是 qn（127/120/116/112/80/74/64/32/16）
    try:
        cfg["bili_qn"] = max(0, int(cfg["bili_qn"]))
    except (TypeError, ValueError):
        cfg["bili_qn"] = DEFAULTS["bili_qn"]
    return cfg


def save(patch: dict) -> dict:
    cfg = load()
    for key in DEFAULTS:
        if key in patch and patch[key] is not None:
            cfg[key] = patch[key]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return load()
