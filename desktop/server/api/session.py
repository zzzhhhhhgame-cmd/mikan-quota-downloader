"""站点会话：手动导入浏览器 Cookie（Cloudflare 兜底通道，无需登录账号）、状态查询与清除。

订阅下载通常不需要任何会话（实测 Chrome 指纹可直连镜像 RSS）；仅当请求被
Cloudflare 拦截时才需要导入。会话文件与 V1 mqd.session.HttpClient 完全兼容。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/session")


@router.get("")
def session_status(request: Request, check: bool = False):
    ctx = request.app.state.ctx
    data = ctx.sessions.status()
    if check:  # 实网校验，默认不触发（避免无谓网络请求）
        data.update(ctx.sessions.check())
    return data


@router.post("/import")
def import_cookies(request: Request, payload: dict):
    """导入浏览器 Cookie 串（k=v; k2=v2），被 CF 拦截时的兜底通道。"""
    ctx = request.app.state.ctx
    cookie_string = (payload.get("cookie_string") or "").strip()
    if not cookie_string:
        raise HTTPException(422, "cookie_string 不能为空")
    return ctx.sessions.import_cookie_string(
        cookie_string, user_agent=payload.get("user_agent") or ""
    )


@router.delete("")
def clear_session(request: Request):
    ctx = request.app.state.ctx
    ctx.sessions.clear()
    return ctx.sessions.status()
