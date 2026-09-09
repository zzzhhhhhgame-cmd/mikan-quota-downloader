"""订阅（RSS 链接）管理：添加即验证并下载、定时检查、启停、删除。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from server.services.subscription_service import SubscriptionError

router = APIRouter(prefix="/api/subscriptions")


@router.get("")
def list_subscriptions(request: Request):
    ctx = request.app.state.ctx
    return {
        "subscriptions": ctx.store.sub_list(),
        "interval_minutes": int(ctx.cfg.setdefault("monitor", {}).get("interval_minutes", 20)),
    }


@router.post("")
def add_subscription(request: Request, payload: dict):
    ctx = request.app.state.ctx
    try:
        return ctx.subs.add(
            payload.get("rss_url") or "",
            title=payload.get("title") or "",
            save_path=payload.get("save_path") or "",
        )
    except SubscriptionError as exc:
        raise HTTPException(422, str(exc))


@router.post("/check-all")
def check_all(request: Request):
    return request.app.state.ctx.subs.check_all()


@router.post("/{sub_id}/check")
def check_subscription(sub_id: int, request: Request):
    ctx = request.app.state.ctx
    try:
        return ctx.subs.check_one(sub_id)
    except SubscriptionError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{sub_id}/rename")
def rename_subscription(sub_id: int, request: Request, payload: dict):
    ctx = request.app.state.ctx
    try:
        return ctx.subs.rename(sub_id, payload.get("title") or "")
    except SubscriptionError as exc:
        status = 404 if "不存在" in str(exc) else 422
        raise HTTPException(status, str(exc))


@router.post("/{sub_id}/toggle")
def toggle_subscription(sub_id: int, request: Request, payload: dict):
    ctx = request.app.state.ctx
    if ctx.store.sub_get(sub_id) is None:
        raise HTTPException(404, f"订阅不存在: {sub_id}")
    enabled = bool(payload.get("enabled"))
    ctx.store.sub_set_enabled(sub_id, enabled)
    return ctx.store.sub_get(sub_id)


@router.delete("/{sub_id}")
def delete_subscription(sub_id: int, request: Request):
    ctx = request.app.state.ctx
    if ctx.store.sub_get(sub_id) is None:
        raise HTTPException(404, f"订阅不存在: {sub_id}")
    ctx.store.sub_delete(sub_id)
    return {"ok": True}
