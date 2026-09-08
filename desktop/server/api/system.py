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
    """运行时调整双限额上限（GB），并回写 config.yaml 使其重启后仍生效。"""
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
    _persist_limits(ctx.cfg, download_gb, seed_gb)
    return quota_snapshot(request)


def _persist_limits(cfg: dict, download_gb, seed_gb):
    """把新限额写回 config.yaml（文件不存在则跳过——测试环境无此文件）。"""
    from pathlib import Path

    import yaml

    path = Path("config.yaml")
    if not path.exists():
        return
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return
    quota = data.setdefault("quota", {})
    if download_gb is not None:
        quota["daily_limit_gb"] = download_gb
    if seed_gb is not None:
        quota["seed_limit_gb"] = seed_gb
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
