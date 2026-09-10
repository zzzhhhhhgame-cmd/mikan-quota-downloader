"""Mikan 镜像/副站域名管理（设置页）：内置种子列表、增删、一键探测。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from server.services.mirrors import MirrorError

router = APIRouter(prefix="/api/mirrors")


@router.get("")
def list_mirrors(request: Request):
    return {"mirrors": request.app.state.ctx.mirrors.list()}


@router.post("")
def add_mirror(request: Request, payload: dict):
    ctx = request.app.state.ctx
    try:
        return ctx.mirrors.add(payload.get("base_url") or "")
    except MirrorError as exc:
        raise HTTPException(422, str(exc))


@router.post("/check")
def check_mirrors(request: Request):
    return request.app.state.ctx.mirrors.check_all()


@router.delete("/{mirror_id}")
def remove_mirror(mirror_id: int, request: Request):
    request.app.state.ctx.mirrors.remove(mirror_id)
    return {"ok": True}
