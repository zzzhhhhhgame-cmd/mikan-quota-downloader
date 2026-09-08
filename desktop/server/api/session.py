"""站点会话：状态查询、手动导入（兜底）、桌面登录向导收割的 HTTP 入口。"""

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
    """兜底通道：手动粘贴浏览器 Cookie 串。"""
    ctx = request.app.state.ctx
    cookie_string = (payload.get("cookie_string") or "").strip()
    if not cookie_string:
        raise HTTPException(422, "cookie_string 不能为空")
    return ctx.sessions.import_cookie_string(
        cookie_string, user_agent=payload.get("user_agent") or ""
    )


@router.post("/harvest")
def harvest(request: Request):
    """触发桌面登录向导的会话收割（仅桌面应用内可用；浏览器返回 501 提示）。"""
    ctx = request.app.state.ctx
    if ctx.harvest_callback is None:
        raise HTTPException(
            501, "当前运行在浏览器模式；请通过桌面应用使用登录向导，或改用 /api/session/import"
        )
    return ctx.harvest_callback()


@router.delete("")
def clear_session(request: Request):
    ctx = request.app.state.ctx
    ctx.sessions.clear()
    return ctx.sessions.status()
