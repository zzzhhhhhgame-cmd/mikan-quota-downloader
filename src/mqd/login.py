"""首次登录工具：python -m mqd.login

弹出真实浏览器窗口，人工完成账号登录与 Cloudflare 人机验证；会话（cookies + UA）
保存到 data/session.json，供后台脚本长期复用。过期后重跑本命令即可。
"""

import json
from pathlib import Path

from .config import load_config


def main():
    cfg = load_config()
    base = cfg["mikan"]["base_url"].rstrip("/")
    out = Path(cfg["mikan"].get("session_file", "data/session.json"))
    profile = Path(cfg["mikan"].get("browser_profile", "data/browser_profile"))
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("缺少 playwright：pip install playwright && playwright install chromium")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(profile), headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"{base}/Account/Login")
        input("请在浏览器中完成登录与 Cloudflare 验证，看到已登录页面后回到终端按回车…")
        user_agent = page.evaluate("navigator.userAgent")
        state = ctx.storage_state()
        ctx.close()

    out.write_text(
        json.dumps({"user_agent": user_agent, "storage_state": state}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"会话已保存到 {out}，后台脚本会自动复用；下次被 Cloudflare 拦截时重跑本命令。")


if __name__ == "__main__":
    main()
