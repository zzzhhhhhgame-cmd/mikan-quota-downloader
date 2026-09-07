"""配置加载：config.yaml（本地，不入 Git）+ 内置默认值合并与必填校验。"""

import copy
import os

import yaml

REQUIRED = (
    ("mikan", "base_url"),
    ("mikan", "rss_url"),
    ("qbittorrent", "base_url"),
)

DEFAULTS = {
    "mikan": {
        "session_file": "data/session.json",
        "cookie_string": "",
        "user_agent": "",
    },
    "qbittorrent": {
        "username": "admin",
        "password": "",
        "category": "bangumi",
        "save_path": "",
        "ratio_limit": None,
    },
    "quota": {"daily_limit_gb": 30},
    "monitor": {"interval_minutes": 20, "db_file": "data/state.db"},
}


def _merge(base, extra):
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path="config.yaml"):
    if not os.path.exists(path):
        raise SystemExit(f"未找到配置文件 {path}：请复制 config.example.yaml 为 config.yaml 并填写")
    with open(path, encoding="utf-8") as f:
        user_cfg = yaml.safe_load(f) or {}
    cfg = _merge(DEFAULTS, user_cfg)
    missing = [f"{section}.{key}" for section, key in REQUIRED if not cfg.get(section, {}).get(key)]
    if missing:
        raise SystemExit("配置缺少必填项: " + ", ".join(missing))
    return cfg
