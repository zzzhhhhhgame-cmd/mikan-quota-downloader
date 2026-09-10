"""桌面应用入口：python desktop/app.py [--browser] [--config config.yaml]

- 默认：pywebview 原生窗口（macOS WKWebView / Windows WebView2）；
- --browser 或 pywebview 不可用时：退化为系统浏览器打开（功能一致）；
- 订阅下载无需 Mikan 账号；仅当被 Cloudflare 拦截时，在「站点 Cookie」卡片
  导入浏览器 Cookie（无需登录）即可，登录向导已于 2026-09-09 移除。
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
            webview.create_window("Anime Downloader", url, width=1380, height=880)
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
