"""桌面应用入口：python desktop/app.py [--browser] [--config config.yaml]

- 默认：pywebview 原生窗口（macOS WKWebView / Windows WebView2）；
- --browser 或 pywebview 不可用时：退化为系统浏览器打开（功能一致，仅无登录收割）；
- 登录向导：设置页点「登录 Mikan」→ 弹出独立窗口打开站点登录页（真浏览器环境，
  可正常通过 Cloudflare）→ 登录完成回来点「我已登录完成」→ 收割 cookies + UA 存入
  data/session.json（与 V1 会话格式互通）。
"""

from __future__ import annotations

import argparse
import pathlib
import socket
import sys
import threading
import time
import urllib.request

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "src"), str(_ROOT / "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class DesktopBridge:
    """暴露给页面 JS 的桥（window.pywebview.api.*）。"""

    def __init__(self, ctx, base_url: str):
        self.ctx = ctx
        self.base_url = base_url
        self.login_window = None

    def open_login_window(self):
        import webview

        if self.login_window is not None:
            try:
                self.login_window.destroy()
            except Exception:
                pass
        login_url = self.ctx.cfg["mikan"]["base_url"].rstrip("/") + "/Account/Login"
        self.login_window = webview.create_window(
            "登录 Mikan（完成后回到应用窗口点「我已登录完成」）",
            login_url,
            width=1100,
            height=780,
        )
        return True

    def harvest_login(self):
        import webview  # noqa: F401  确认可用性

        window = self.login_window
        if window is None:
            return {"ok": False, "error": "请先点击「登录 Mikan」打开登录窗口并完成登录"}
        try:
            cookies = window.get_cookies() or []
            user_agent = window.evaluate_js("navigator.userAgent") or ""
        except Exception as exc:  # WebView2 旧版本可能不支持 get_cookies
            return {"ok": False, "error": f"收割失败（可改用手动导入 Cookie）：{exc}"}
        from server.services.session_manager import cookiejar_cookie_to_dict

        cookie_dicts = [cookiejar_cookie_to_dict(c) for c in cookies]
        status = self.ctx.sessions.save(user_agent, {"cookies": cookie_dicts})
        try:
            window.destroy()
        finally:
            self.login_window = None
        return {"ok": True, "cookies": len(cookie_dicts), "status": status}


def _wait_server_ready(url: str, timeout_s: float = 15.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "api/health", timeout=1) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("本地服务启动超时")


def _start_server(app, port: int):
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    return server


def main():
    parser = argparse.ArgumentParser(description="mikan-quota-downloader 桌面端")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--browser", action="store_true", help="用系统浏览器代替应用窗口")
    args = parser.parse_args()

    from mqd.config import load_config

    from server.main import create_app
    from server.services.app_context import build_context

    ctx = build_context(load_config(args.config), config_path=args.config)
    app = create_app(ctx)
    port = _free_port()
    _start_server(app, port)
    url = f"http://127.0.0.1:{port}/"
    _wait_server_ready(url)

    try:
        ctx.start()
    except Exception as exc:  # 引擎起不来（如 qBt 未开 WebUI）也先让 UI 可用，便于排障
        print(f"[警告] 引擎启动失败：{exc}", file=sys.stderr)

    webview = None
    try:
        if not args.browser:
            import webview as _webview

            webview = _webview
    except ImportError:
        print("[提示] 未安装 pywebview（pip install pywebview），改用浏览器模式", file=sys.stderr)

    try:
        if webview is not None:
            bridge = DesktopBridge(ctx, url)
            ctx.harvest_callback = bridge.harvest_login
            window = webview.create_window(
                "追番下载器 · mikan-quota-downloader", url, js_api=bridge, width=1380, height=880
            )
            webview.start()  # 阻塞至窗口关闭
        else:
            import webbrowser

            print(f"应用已启动：{url}（Ctrl+C 退出）", flush=True)
            webbrowser.open(url)
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
