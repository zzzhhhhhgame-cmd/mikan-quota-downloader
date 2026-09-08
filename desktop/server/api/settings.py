"""应用设置：引擎信息、默认下载目录、双限额、直连绑定。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from mqd.quota import GiB

router = APIRouter(prefix="/api/settings")


@router.get("")
def get_settings(request: Request):
    ctx = request.app.state.ctx
    desktop_cfg = ctx.cfg.get("desktop", {})
    return {
        "engine": type(ctx.engine).__name__,
        "save_path": ctx.default_save_path,
        "bind_ip": desktop_cfg.get("bind_ip") or "",
        "bt_port": int(desktop_cfg.get("bt_port", 6881)),
        "download_limit_gb": ctx.guard.quota.limit / GiB,
        "seed_limit_gb": ctx.guard.seed_limit / GiB,
        "interval_minutes": int(ctx.cfg.setdefault("monitor", {}).get("interval_minutes", 20)),
        "config_persist": bool(ctx.config_store is not None and ctx.config_store.path),
    }


@router.post("/interval")
def set_interval(request: Request, payload: dict):
    """订阅检查间隔（分钟），立即生效并回写 config.yaml。"""
    ctx = request.app.state.ctx
    try:
        minutes = int(payload.get("minutes"))
    except (TypeError, ValueError):
        raise HTTPException(422, "minutes 必须是整数")
    if minutes < 1:
        raise HTTPException(422, "间隔至少 1 分钟")
    ctx.cfg.setdefault("monitor", {})["interval_minutes"] = minutes
    if ctx.config_store is not None:
        ctx.config_store.update("monitor", {"interval_minutes": minutes})
    if ctx.scheduler is not None:
        ctx.scheduler.set_interval(minutes)
    return {"ok": True, "interval_minutes": minutes}


@router.post("/save-path")
def set_save_path(request: Request, payload: dict):
    """设置全局默认下载目录：绝对路径、自动创建、回写 config.yaml。

    只影响之后新增的任务；已在途任务保持各自原有目录。
    """
    ctx = request.app.state.ctx
    raw = (payload.get("save_path") or "").strip()
    if not raw:
        raise HTTPException(422, "save_path 不能为空")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise HTTPException(422, f"必须是绝对路径：{raw}")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(422, f"目录无法创建：{exc}")
    ctx.default_save_path = str(path)
    if ctx.config_store is not None:
        ctx.config_store.update("desktop", {"save_path": str(path)})
    return get_settings(request)
