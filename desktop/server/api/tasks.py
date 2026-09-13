"""任务（引擎任务）查看与控制，以及全局限速。"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request

from mqd.torrents import infohash_from_bytes

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


@router.post("/tasks/add")
async def add_task(request: Request, save_path: str = "", sequential: bool = False):
    """手动添加种子（请求体 = .torrent 原始字节），同样受每日限额约束。

    目录优先级：请求参数 save_path > 应用默认下载目录；两者都为空时明确报错，
    不放任任务落到随机目录。
    """
    ctx = request.app.state.ctx
    raw = await request.body()
    if not raw:
        raise HTTPException(422, "请求体为空：请上传 .torrent 文件内容")
    target = save_path.strip() or ctx.default_save_path
    if not target:
        raise HTTPException(422, "未指定下载目录：请先在设置中保存默认下载目录，或随请求指定 save_path")
    try:
        decision = ctx.guard.admit(raw, save_path=target, sequential=sequential)
    except ValueError:
        raise HTTPException(422, "不是有效的 .torrent 文件")
    except EngineError as exc:
        raise HTTPException(422, str(exc))
    return {
        "sha": infohash_from_bytes(raw),
        "started": decision.start,
        "reason": decision.reason,
        "save_path": target,
    }


@router.post("/tasks/add-magnet")
def add_magnet_task(request: Request, payload: dict):
    """手动添加磁力链接（无需 .torrent 文件），同样受每日下载限额约束。

    磁链的体积在元数据到达后才可知：先以运行状态添加以获取元数据（流量极小），
    QuotaGuard 在体积已知后补充限额判定——超出预算会自动暂停进入等待队列。
    """
    ctx = request.app.state.ctx
    uri = (payload.get("magnet") or "").strip()
    if not uri.lower().startswith("magnet:?"):
        raise HTTPException(422, "请粘贴以 magnet:?xt=urn:btih: 开头的磁力链接")
    target = (payload.get("save_path") or "").strip() or ctx.default_save_path
    if not target:
        raise HTTPException(422, "未指定下载目录：请先在设置中保存默认下载目录，或随请求指定 save_path")
    try:
        sha = ctx.engine.add_magnet(
            uri, paused=False, save_path=target, sequential=bool(payload.get("sequential"))
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except EngineError as exc:
        raise HTTPException(503, f"引擎添加失败：{exc}")
    return {"sha": sha, "save_path": target, "sequential": bool(payload.get("sequential"))}


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
    # 手动删除的集数视为不需要：封存后不再被订阅自动恢复/做种
    ctx.store.episode_seal_by_sha(sha)
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
