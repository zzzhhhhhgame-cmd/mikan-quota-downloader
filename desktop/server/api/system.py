"""系统健康与限额配置。"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request

from mqd.quota import DailyQuota, GiB

router = APIRouter(prefix="/api")


@router.get("/health")
def health(request: Request):
    ctx = request.app.state.ctx
    return {"status": "ok", "engine": type(ctx.engine).__name__}


@router.get("/quota")
def quota_snapshot(request: Request):
    ctx = request.app.state.ctx
    try:
        snap = ctx.guard.snapshot()
    except Exception as exc:
        raise HTTPException(503, f"引擎不可达：{exc}")
    data = asdict(snap)
    data["remaining"] = snap.remaining
    data["seed_gate_closed"] = snap.seed_gate_closed
    return data


@router.post("/quota/limits")
def update_limits(request: Request, payload: dict):
    """运行时调整双限额上限（GB），并回写实际加载的 config.yaml。"""
    ctx = request.app.state.ctx
    download_gb = payload.get("download_limit_gb")
    seed_gb = payload.get("seed_limit_gb")
    try:
        if download_gb is not None:
            ctx.guard.quota = DailyQuota(int(float(download_gb) * GiB))
        if seed_gb is not None:
            ctx.guard.seed_limit = int(float(seed_gb) * GiB)
    except (TypeError, ValueError):
        raise HTTPException(422, "限额必须是非负数字（GB）")
    values = {}
    if download_gb is not None:
        values["daily_limit_gb"] = download_gb
    if seed_gb is not None:
        values["seed_limit_gb"] = seed_gb
    if ctx.config_store is not None and values:
        ctx.config_store.update("quota", values)
    return quota_snapshot(request)
