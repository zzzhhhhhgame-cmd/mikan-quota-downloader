"""调度器测试：启动后立即执行第一轮（不等间隔）。"""

import time
import unittest

from server.services.scheduler import SyncScheduler


class ImmediateGuard:
    def __init__(self):
        self.syncs = 0

    def sync(self):
        self.syncs += 1
        return ([], False)


class SchedulerImmediateTest(unittest.TestCase):
    def test_start_runs_once_immediately(self):
        guard = ImmediateGuard()
        sched = SyncScheduler(guard, interval_minutes=60)
        sched.start()
        try:
            deadline = time.time() + 2
            while time.time() < deadline and sched.last_result is None:
                time.sleep(0.02)
            self.assertIsNotNone(sched.last_result)
        finally:
            sched.stop()

    def test_set_interval_takes_effect(self):
        sched = SyncScheduler(ImmediateGuard(), interval_minutes=60)
        sched.set_interval(5)
        self.assertEqual(sched._interval_s, 300)
        sched.stop()


if __name__ == "__main__":
    unittest.main()
