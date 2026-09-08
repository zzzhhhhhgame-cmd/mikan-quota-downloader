"""订阅轮询调度器（M2 骨架）：按间隔触发限额同步；M3 注入订阅抓取任务。"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("mqd.scheduler")


class SyncScheduler:
    def __init__(self, guard, interval_minutes: int = 20, mikan_job=None):
        self._guard = guard
        self._interval_s = max(1, int(interval_minutes)) * 60
        self._mikan_job = mikan_job  # M3：订阅轮询（拉 RSS → admit 新集）
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_result: dict | None = None

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="mqd-scheduler", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def run_once(self) -> dict:
        resumed, gate_closed = self._guard.sync()
        self.last_result = {
            "resumed": resumed,
            "seed_gate_closed": gate_closed,
        }
        log.info(
            "调度完成：放行 %d 个任务，做种闸门%s",
            len(resumed),
            "关闭" if gate_closed else "开启",
        )
        return self.last_result

    def _loop(self):
        while not self._stop.wait(self._interval_s):
            try:
                self.run_once()
            except Exception:  # 单轮失败不影响常驻
                log.exception("调度轮次失败")
