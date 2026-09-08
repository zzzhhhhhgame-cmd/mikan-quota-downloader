"""任务（引擎任务）查看与控制，以及全局限速。"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request

from server.engine.base import EngineError

router = APIRouter(prefix="/api")


def _serialize(t):
    data = asdict(t)
    data["state"] = t.state.value
    return data


@router.get("/tasks")
def list_tasks(request: Request):
    ctx = request.app.state.ctx
    try:
        tasks = ctx.engine.list()
    except Exception as exc:
        raise HTTPException(503, f"引擎不可达：{exc}")
    return [_serialize(t) for t in tasks]


@router.post("/tasks/{sha}/pause")
def pause_task(sha: str, request: Request):
    ctx = request.app.state.ctx
    try:
        ctx.engine.pause(sha)
    except EngineError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True}


@router.post("/tasks/{sha}/resume")
def resume_task(sha: str, request: Request):
    ctx = request.app.state.ctx
    try:
        ctx.engine.resume(sha)
    except EngineError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True}


@router.delete("/tasks/{sha}")
def remove_task(sha: str, request: Request, with_files: bool = False):
    ctx = request.app.state.ctx
    try:
        ctx.engine.remove(sha, with_files=with_files)
    except EngineError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True}


@router.post("/rate-limit")
def set_rate_limit(request: Request, payload: dict):
    """用户的全局限速（B/s；null=不限）。与容量限额相互独立。"""
    ctx = request.app.state.ctx
    down = payload.get("down_bps")
    up = payload.get("up_bps")
    if down is not None:
        ctx.engine.set_download_limit(int(down))
    if up is not None:
        ctx.engine.set_upload_limit(int(up))
    return {"ok": True}
