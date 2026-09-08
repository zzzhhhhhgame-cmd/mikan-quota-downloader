"""会话管理：data/session.json 的存取与状态校验。

文件格式与 V1 mqd.session.HttpClient 完全兼容（user_agent + playwright 风格
storage_state），登录来源可以是桌面应用的 webview 收割、Playwright 向导或手动导入。
"""

from __future__ import annotations

import json
from pathlib import Path


def cookiejar_cookie_to_dict(cookie) -> dict:
    """把 pywebview 返回的 cookie（cookiejar.Cookie 或 dict）统一成 storage_state 形态。"""
    if isinstance(cookie, dict):
        return {
            "name": cookie.get("name", ""),
            "value": cookie.get("value", ""),
            "domain": cookie.get("domain", ""),
            "path": cookie.get("path", "/"),
        }
    return {
        "name": cookie.name,
        "value": cookie.value,
        "domain": cookie.domain or "",
        "path": cookie.path or "/",
    }


class SessionManager:
    def __init__(self, base_url: str, session_file: str):
        self.base_url = base_url.rstrip("/")
        self.session_file = Path(session_file)

    # ---- 状态与存取 ----

    def status(self) -> dict:
        exists = self.session_file.exists()
        return {
            "configured": exists,
            "file": str(self.session_file),
            "base_url": self.base_url,
        }

    def load(self) -> dict | None:
        if not self.session_file.exists():
            return None
        return json.loads(self.session_file.read_text(encoding="utf-8"))

    def save(self, user_agent: str, storage_state: dict) -> dict:
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.session_file.write_text(
            json.dumps(
                {"user_agent": user_agent or "", "storage_state": storage_state or {}},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return self.status()

    def import_cookie_string(self, cookie_string: str, user_agent: str = "") -> dict:
        """兜底通道：手动粘贴的 Cookie 串（k=v; k2=v2）转成会话文件。"""
        cookies = []
        for part in (cookie_string or "").split(";"):
            if "=" in part:
                name, value = part.strip().split("=", 1)
                cookies.append({"name": name, "value": value, "domain": "", "path": "/"})
        return self.save(user_agent, {"cookies": cookies})

    def clear(self):
        if self.session_file.exists():
            self.session_file.unlink()

    # ---- 实网校验（可能触发网络请求与 Cloudflare，勿在测试中调用）----

    def check(self) -> dict:
        saved = self.load()
        if not saved:
            return {"ok": False, "reason": "尚未配置会话"}
        from mqd.session import CloudflareBlocked, HttpClient

        try:
            HttpClient(self.base_url, session_file=str(self.session_file)).get("/")
        except CloudflareBlocked as exc:
            return {"ok": False, "reason": str(exc)}
        except Exception as exc:  # 网络不可达等
            return {"ok": False, "reason": f"网络错误: {exc}"}
        return {"ok": True, "reason": ""}
