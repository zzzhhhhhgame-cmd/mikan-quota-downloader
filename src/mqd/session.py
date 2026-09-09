"""HTTP 会话层：浏览器指纹 + 首次登录保存的会话复用 + Cloudflare 拦截识别。

注意：过盾得到的 cf_clearance 绑定 IP 与 User-Agent，因此 UA 必须与登录时一致。
"""

import json
from pathlib import Path
from urllib.parse import urlparse

CF_MARKS = ("just a moment", "challenge-platform", "cf-chl", "attention required")


class CloudflareBlocked(RuntimeError):
    """Cloudflare 人机验证拦截：需要在『站点 Cookie』卡片导入浏览器 Cookie（无需登录）。"""


class HttpClient:
    """带 Chrome TLS 指纹的 HTTP 客户端，自动加载 data/session.json 中的会话。"""

    def __init__(self, base_url, session_file=None, cookie_string=None, user_agent=None):
        from curl_cffi import requests as creq

        self.base = base_url.rstrip("/")
        self.host = urlparse(self.base).hostname or ""
        self.session = creq.Session(impersonate="chrome")

        ua = user_agent
        if session_file and Path(session_file).exists():
            saved = json.loads(Path(session_file).read_text(encoding="utf-8"))
            ua = saved.get("user_agent") or ua
            self._load_storage_state(saved.get("storage_state") or {})
        elif cookie_string:
            for part in cookie_string.split(";"):
                if "=" in part:
                    name, value = part.strip().split("=", 1)
                    self.session.cookies.set(name, value)
        if ua:
            self.session.headers["User-Agent"] = ua

    def _load_storage_state(self, state):
        for cookie in state.get("cookies", []):
            domain = (cookie.get("domain") or "").lstrip(".")
            if not domain or domain in self.host or self.host.endswith(domain):
                self.session.cookies.set(cookie["name"], cookie["value"], domain=domain or None)

    @staticmethod
    def _guard(resp):
        try:
            head = resp.text[:2048].lower()
        except Exception:
            head = ""
        if resp.status_code in (403, 429, 503) or any(mark in head for mark in CF_MARKS):
            raise CloudflareBlocked(
                f"Cloudflare 拦截了请求（HTTP {resp.status_code}）。"
                "请在『站点 Cookie』卡片导入浏览器 Cookie（无需登录账号）。"
            )

    def get(self, path_or_url):
        url = path_or_url if path_or_url.startswith("http") else self.base + path_or_url
        resp = self.session.get(url, timeout=30)
        self._guard(resp)
        resp.raise_for_status()
        return resp.content

    def get_text(self, path_or_url):
        return self.get(path_or_url).decode("utf-8", "replace")
