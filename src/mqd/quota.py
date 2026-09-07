"""每日限额判定。

预算 = 每日上限 − 今日已用 − 在途种子剩余量。
新种子装得下立即开始，装不下进等待队列（由 main 添加为暂停）。
"""

from dataclasses import dataclass

GiB = 1024 ** 3


@dataclass
class Decision:
    start: bool
    reason: str


class DailyQuota:
    def __init__(self, limit_bytes):
        self.limit = limit_bytes

    def remaining(self, used_today, active_remaining):
        return max(0, self.limit - used_today - active_remaining)

    def decide(self, new_size, used_today, active_remaining) -> Decision:
        budget = self.remaining(used_today, active_remaining)
        if new_size <= budget:
            return Decision(True, f"新种子 {new_size / GiB:.2f}GiB ≤ 剩余预算 {budget / GiB:.2f}GiB，直接开始")
        return Decision(
            False,
            f"新种子 {new_size / GiB:.2f}GiB > 剩余预算 {budget / GiB:.2f}GiB，进入等待队列",
        )
