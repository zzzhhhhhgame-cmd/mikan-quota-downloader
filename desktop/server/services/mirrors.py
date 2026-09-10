"""Mikan 镜像/副站域名管理：内置常见域名种子，可增删、可探测，供订阅故障转移。

域名排序规则：最近一次探测可用的排最前，其余按添加顺序——订阅拉取时依次尝试，
成功的域名会记录到订阅（mirror_host），下次优先使用。
"""

from __future__ import annotations

from urllib.parse import urlsplit

# 内置种子列表（2026-09 检索整理；可在设置页增删，探测不可用的会被自动排后）
DEFAULT_MIRRORS = [
    "https://mikan.tangbai.cc",   # 用户在用，实测可用
    "https://mikanime.tv",        # 官方新域名
    "https://mikanani.me",        # 官方主站（部分地区被墙）
    "https://mikan.sakiko.de",    # 社区镜像（ANI-RSS 文档推荐）
    "https://mikanani.kas.pub",   # 社区镜像
]


def normalize_base(url: str) -> str:
    """把用户输入规整为 scheme://host 形式（允许不带 https:// 前缀）。"""
    url = (url or "").strip()
    if not url:
        raise ValueError("域名不能为空")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parts = urlsplit(url)
    if not parts.netloc:
        raise ValueError(f"无法解析域名: {url}")
    return f"{parts.scheme}://{parts.netloc}"


def _default_probe(base_url: str) -> str | None:
    """探测镜像可达性：返回 None=可用，否则为失败原因。"""
    from mqd.session import CloudflareBlocked, HttpClient

    try:
        HttpClient(base_url).get("/")
    except CloudflareBlocked as exc:
        return str(exc)
    except Exception as exc:
        return f"连接失败：{exc}"
    return None


class MirrorError(RuntimeError):
    pass


class MirrorService:
    def __init__(self, store, probe=None):
        self.store = store
        self.probe = probe or _default_probe

    def ensure_seeded(self):
        for base in DEFAULT_MIRRORS:
            self.store.mirror_add(base)

    def list(self):
        return self.store.mirror_list()

    def add(self, base_url: str) -> dict:
        try:
            base = normalize_base(base_url)
        except ValueError as exc:
            raise MirrorError(str(exc)) from exc
        mirror_id = self.store.mirror_add(base)
        error = self.probe(base)
        self.store.mirror_mark(mirror_id, ok=error is None, error=error or "")
        row = next(r for r in self.store.mirror_list() if r["id"] == mirror_id)
        return {**row, "available": error is None, "detail": error or "可用"}

    def remove(self, mirror_id: int):
        self.store.mirror_remove(mirror_id)

    def check_all(self) -> dict:
        ok = 0
        rows = self.store.mirror_list()
        for row in rows:
            error = self.probe(row["base_url"])
            self.store.mirror_mark(row["id"], ok=error is None, error=error or "")
            if error is None:
                ok += 1
        return {"checked": len(rows), "available": ok}

    def ordered_hosts(self):
        return self.store.mirror_hosts()