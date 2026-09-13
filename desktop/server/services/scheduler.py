"""订阅轮询调度器：按间隔触发限额同步 + 订阅检查；启动立即执行第一轮。

全局暂停时（should_run 返回 False）跳过整轮：不拉订阅、不入队新任务，
引擎层的全部传输也已被暂停。
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("mqd.scheduler")


class SyncScheduler:
    def __init__(self, guard, interval_minutes: int = 20, mikan_job=None, should_run=None):
        self._guard = guard
        self._interval_s = max(1, int(interval_minutes)) * 60
        self._mikan_job = mikan_job  # 订阅轮询任务（拉 RSS → 新集 → 限额入队）
        self._should_run = should_run  # () -> bool；False 时本轮跳过（全局暂停中）
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_result: dict | None = None

    def set_interval(self, minutes: int):
        """运行时调整检查间隔（分钟），下一轮生效。"""
        self._interval_s = max(1, int(minutes)) * 60

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
        self.last_result = {"resumed": resumed, "seed_gate_closed": gate_closed}
        if self._mikan_job is not None:
            try:
                self.last_result["subscriptions"] = self._mikan_job()
            except Exception:
                log.exception("订阅检查失败")
        log.info(
            "调度完成：放行 %d 个任务，做种闸门%s",
            len(resumed),
            "关闭" if gate_closed else "开启",
        )
        return self.last_result

    def _loop(self):
        try:
            self.run_once()  # 启动立即执行第一轮：恢复做种/补拉丢失任务不等间隔
        except Exception:
            log.exception("启动轮次失败")
        while not self._stop.wait(self._interval_s):
            if self._should_run is not None and not self._should_run():
                log.info("全局暂停中，本轮跳过")
                continue
            try:
                self.run_once()
            except Exception:
                log.exception("调度轮次失败")
