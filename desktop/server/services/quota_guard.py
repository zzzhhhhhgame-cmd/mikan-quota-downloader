"""限额守门人：V1 的每日 30GB 决策逻辑的事件驱动版。

置于引擎之上：新集到达时做「立即开始 / 排队」判定；预算变化（种子完成、跨天重置）
时按加入顺序放行等待队列。记账沿用 V1 的 store.py（SQLite 按自然日归集）。

注意：引擎里的 PAUSED 统一视为"等待队列"，人工暂停与限额排队在 M4 的 UI 层再区分。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from mqd.quota import DailyQuota, Decision, GiB
from mqd.store import Store
from mqd.torrents import torrent_size

from ..engine.base import TaskState


@dataclass
class QuotaSnapshot:
    """一次限额评估的结果，UI 看板直接消费。"""

    day: str
    limit: int
    used_today: int
    active_remaining: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used_today - self.active_remaining)


class QuotaGuard:
    def __init__(self, engine, store: Store, quota: DailyQuota | None = None):
        self.engine = engine
        self.store = store
        self.quota = quota or DailyQuota(30 * GiB)

    def snapshot(self) -> QuotaSnapshot:
        """从引擎取任务状态，归集今日下载量并计算在途剩余。"""
        day = time.strftime("%Y-%m-%d")
        torrents = self.engine.list()
        used_today = self.store.attribute_all(
            [
                {"hash": t.sha, "size": t.size, "downloaded": t.downloaded}
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
            limit=self.quota.limit,
            used_today=used_today,
            active_remaining=active_remaining,
        )

    def admit(self, raw: bytes, *, save_path: str, sequential: bool = False) -> Decision:
        """新集入队：预算装得下立即开始，装不下以暂停态入队，等待 drain() 放行。"""
        size = torrent_size(raw)
        snap = self.snapshot()
        decision = self.quota.decide(size, snap.used_today, snap.active_remaining)
        self.engine.add(raw, paused=not decision.start, save_path=save_path, sequential=sequential)
        return decision

    def drain(self) -> list[str]:
        """预算允许时按加入先后放行等待队列（小任务可插空），返回恢复的任务 sha。"""
        snap = self.snapshot()
        active_remaining = snap.active_remaining
        resumed = []
        waiting = sorted(
            (t for t in self.engine.list() if t.state == TaskState.PAUSED),
            key=lambda t: (t.added_at, t.order),
        )
        for t in waiting:
            need = max(0, t.size - t.done)
            if need <= self.quota.remaining(snap.used_today, active_remaining):
                self.engine.resume(t.sha)
                active_remaining += need
                resumed.append(t.sha)
        return resumed
