"""限额守门人：下载量 + 做种上传量双日限额（上限均可自定义）。

- 下载限额：置于引擎之上做入队决策——新集装得下立即开始，装不下排队（暂停态），
  预算变化（种子完成、跨天重置）时按加入顺序放行（`drain()`）；
- 做种限额：只做当日「上传闸门」——触达上限时把全局上传限速压到闸门值（≈停止上传），
  次日/用量回落自动放开并还原用户设定的上传限速（`apply_seed_policy()`）。

记账沿用 store.py（SQLite 按自然日归集，含 uploaded 列）。注意：引擎里的 PAUSED
统一视为"等待队列"，人工暂停与限额排队在 M4 的 UI 层再区分。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from mqd.quota import DailyQuota, Decision, GiB
from mqd.store import Store
from mqd.torrents import torrent_size

from ..engine.base import TaskState

GATE_BPS = 1  # 上传闸门值：1 B/s ≈ 停止上传（引擎里 0=不限速，不能用 0）


@dataclass
class QuotaSnapshot:
    """一次双限额评估的结果，UI 看板直接消费。"""

    day: str
    download_limit: int
    used_download: int
    active_remaining: int
    seed_limit: int  # 0=不限
    used_upload: int

    @property
    def remaining(self) -> int:
        """下载剩余预算（字节）。"""
        return max(0, self.download_limit - self.used_download - self.active_remaining)

    @property
    def seed_gate_closed(self) -> bool:
        return self.seed_limit > 0 and self.used_upload >= self.seed_limit


class QuotaGuard:
    def __init__(
        self,
        engine,
        store: Store,
        download_quota: DailyQuota | None = None,
        seed_limit_bytes: int = 0,
        user_upload_bps: int | None = None,
    ):
        self.engine = engine
        self.store = store
        self.quota = download_quota or DailyQuota(30 * GiB)
        self.seed_limit = int(seed_limit_bytes or 0)
        self.user_upload_bps = user_upload_bps  # 用户在设置页配置的上传限速；None=不限

    def snapshot(self) -> QuotaSnapshot:
        """从引擎取任务状态，归集今日下载/上传量并计算在途剩余。"""
        day = time.strftime("%Y-%m-%d")
        torrents = self.engine.list()
        usage = self.store.attribute_all(
            [
                {
                    "hash": t.sha,
                    "size": t.size,
                    "downloaded": t.downloaded,
                    "uploaded": t.uploaded,
                }
                for t in torrents
            ],
            day,
        )
        active_remaining = sum(
            max(0, t.size - t.done)
            for t in torrents
            if t.state not in (TaskState.COMPLETED, TaskState.FAILED)
        )
        return QuotaSnapshot(
            day=day,
            download_limit=self.quota.limit,
            used_download=usage.down,
            active_remaining=active_remaining,
            seed_limit=self.seed_limit,
            used_upload=usage.up,
        )

    def admit(self, raw: bytes, *, save_path: str, sequential: bool = False) -> Decision:
        """新集入队：下载预算装得下立即开始，装不下以暂停态入队，等待 drain() 放行。"""
        size = torrent_size(raw)
        snap = self.snapshot()
        decision = self.quota.decide(size, snap.used_download, snap.active_remaining)
        self.engine.add(raw, paused=not decision.start, save_path=save_path, sequential=sequential)
        return decision

    def drain(self) -> list[str]:
        """下载预算允许时按加入先后放行等待队列（小任务可插空），返回恢复的任务 sha。"""
        snap = self.snapshot()
        active_remaining = snap.active_remaining
        resumed = []
        waiting = sorted(
            (t for t in self.engine.list() if t.state == TaskState.PAUSED),
            key=lambda t: (t.added_at, t.order),
        )
        for t in waiting:
            need = max(0, t.size - t.done)
            if need <= self.quota.remaining(snap.used_download, active_remaining):
                self.engine.resume(t.sha)
                active_remaining += need
                resumed.append(t.sha)
        return resumed

    def apply_seed_policy(self) -> bool:
        """应用做种限额闸门，返回闸门是否处于关闭状态。"""
        snap = self.snapshot()
        if snap.seed_gate_closed:
            self.engine.set_upload_limit(GATE_BPS)
            return True
        self.engine.set_upload_limit(self.user_upload_bps)  # None=不限速
        return False

    def sync(self):
        """调度器每轮调用：放行下载等待队列 + 应用做种闸门。返回 (恢复的 sha, 闸门是否关闭)。"""
        resumed = self.drain()
        gate_closed = self.apply_seed_policy()
        return resumed, gate_closed
