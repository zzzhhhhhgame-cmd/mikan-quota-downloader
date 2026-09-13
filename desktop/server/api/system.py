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


@router.get("/stats")
def live_stats(request: Request):
    """实时监视：BT 上/下行速度（引擎各任务实时速率之和）+ 应用进程内存/CPU 占用。"""
    ctx = request.app.state.ctx
    down = up = 0
    try:
        for t in ctx.engine.list():
            down += t.rate_down
            up += t.rate_up
    except Exception:
        pass  # 引擎不可达时按 0 显示，界面有「引擎不可达」提示
    mem_mb = cpu = None
    try:
        import os

        import psutil

        proc = psutil.Process(os.getpid())
        mem_mb = round(proc.memory_info().rss / 1048576, 1)
        cpu = round(proc.cpu_percent(interval=None), 1)
    except Exception:
        pass  # 未安装 psutil 时省略性能占用
    return {"down_bps": down, "up_bps": up, "mem_mb": mem_mb, "cpu_percent": cpu,
            "paused": bool(getattr(ctx, "paused", False))}


@router.post("/pause")
def set_global_pause(request: Request, payload: dict):
    """全局停止总开关：暂停/恢复所有下载与做种（状态持久化，重启保持）。

    暂停期间：引擎停止全部传输，调度器跳过订阅检查；恢复后立即补拉/放行。
    """
    ctx = request.app.state.ctx
    ctx.set_paused(bool(payload.get("paused")))
    if not ctx.paused and ctx.scheduler is not None:
        try:
            ctx.scheduler.run_once()
        except Exception:
            pass
    return {"ok": True, "paused": ctx.paused}


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
