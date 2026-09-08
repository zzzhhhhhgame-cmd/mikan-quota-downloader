"""config.yaml 的有限回写：只更新指定小节的白名单键；path=None 时为 no-op。

应用回写路径必须来自实际加载的配置文件（避免误写别处的 config.yaml）；
测试环境不传路径，所有回写自动跳过。
"""

from __future__ import annotations

from pathlib import Path


class ConfigStore:
    def __init__(self, path: str | None):
        self.path = Path(path) if path else None

    def update(self, section: str, values: dict) -> bool:
        if self.path is None or not self.path.exists():
            return False
        import yaml

        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        data.setdefault(section, {}).update(values)
        self.path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        return True
