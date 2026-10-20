"""可热更新的解析规则。

`data/rules.json` 里的配置优先于内置默认值；改完立即生效，不用重新打包。
只允许覆盖已知字段，避免一份坏规则把程序打瘫。
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from .paths import data_dir

RULES_PATH = data_dir() / "rules.json"

DEFAULTS = {
    "version": 1,
    "updated_at": 0,
    "douyin": {
        "share_targets": [
            {"template": "https://m.douyin.com/share/{kind}/{aweme_id}", "ua": "mobile"},
            {"template": "https://www.iesdouyin.com/share/{kind}/{aweme_id}/", "ua": "mobile"},
            {"template": "https://www.iesdouyin.com/share/{kind}/{aweme_id}/", "ua": "desktop"},
        ],
        "browser_fallback": True,
    },
    "bilibili": {"fallback": True},
}

_MAX_TARGETS = 8


def _merge(base: dict, patch: dict) -> dict:
    result = dict(base)
    for key, value in (patch or {}).items():
        if key not in base:
            continue
        if isinstance(base[key], dict) and isinstance(value, dict):
            result[key] = _merge(base[key], value)
        elif isinstance(base[key], list) and isinstance(value, list):
            result[key] = value
        elif isinstance(base[key], bool):
            result[key] = bool(value)
        elif isinstance(base[key], (int, float)) and isinstance(value, (int, float)):
            result[key] = value
        elif isinstance(base[key], str) and isinstance(value, str):
            result[key] = value
    return result


def load() -> dict:
    rules = json.loads(json.dumps(DEFAULTS))
    try:
        raw = json.loads(RULES_PATH.read_text("utf-8"))
        if isinstance(raw, dict):
            rules = _merge(rules, raw)
    except (OSError, ValueError):
        pass
    targets = rules["douyin"].get("share_targets") or DEFAULTS["douyin"]["share_targets"]
    clean = []
    for target in targets[:_MAX_TARGETS]:
        if not isinstance(target, dict):
            continue
        template = str(target.get("template") or "")
        if "{aweme_id}" not in template:
            continue
        clean.append({"template": template,
                      "ua": "mobile" if target.get("ua") == "mobile" else "desktop"})
    rules["douyin"]["share_targets"] = clean or DEFAULTS["douyin"]["share_targets"]
    return rules


def save(patch: dict) -> dict:
    rules = load()
    merged = _merge(rules, patch if isinstance(patch, dict) else {})
    merged["updated_at"] = int(time.time())
    RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    RULES_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return load()


def fetch(url: str, timeout: float = 20) -> dict:
    """从一个 JSON 地址热更新规则（只接受已知字段）。"""
    request = urllib.request.Request(
        str(url), headers={"User-Agent": "DouyinKit-Rules/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(512 * 1024)
    data = json.loads(raw.decode("utf-8", "replace"))
    if not isinstance(data, dict):
        raise ValueError("规则文件必须是 JSON 对象")
    return save(data)


def auto_fallback_enabled() -> bool:
    return bool(load()["douyin"].get("browser_fallback", True))


__all__ = ["DEFAULTS", "RULES_PATH", "auto_fallback_enabled", "fetch", "load",
           "save"]
