"""QuotaGuard 单测：限额判定、排队与按序放行、跨天重置。"""

import tempfile
import time
import unittest
from unittest import mock

from mqd.quota import DailyQuota, GiB
from mqd.store import Store
from mqd.torrents import infohash_from_bytes, torrent_size

from server.engine.base import Engine, TaskState, TorrentState
from server.services.quota_guard import QuotaGuard


def make_torrent(length: int, tag: bytes = b"a1") -> bytes:
    """构造单文件 bencode 种子（tag 恒 2 字节，保证编码合法）。"""
    return b"d4:infod6:lengthi" + str(length).encode() + b"e4:name2:" + tag + b"ee"


class FakeEngine(Engine):
    """内存引擎：只维护状态机，供限额层测试。"""

    def __init__(self):
        self.torrents = []
        self.resumed = []
        self._seq = 0

    def start(self):
        pass

    def stop(self):
        pass

    def add(self, data, *, paused, save_path, sequential=False, category=""):
        sha = infohash_from_bytes(data)
        for t in self.torrents:
            if t.sha == sha:
                return sha
        self._seq += 1
        self.torrents.append(
            TorrentState(
                sha=sha,
                name="t%d" % self._seq,
                state=TaskState.PAUSED if paused else TaskState.DOWNLOADING,
                size=torrent_size(data),
                save_path=save_path,
                sequential=sequential,
                added_at=time.time() + self._seq / 1000.0,
                order=self._seq,
            )
        )
        return sha

    def pause(self, sha):
        self._set(sha, state=TaskState.PAUSED)

    def resume(self, sha):
        self.resumed.append(sha)
        self._set(sha, state=TaskState.DOWNLOADING)

    def remove(self, sha, with_files=False):
        self.torrents = [t for t in self.torrents if t.sha != sha]

    def list(self):
        return list(self.torrents)

    def set_global_limit(self, down_bps):
        pass

    def _set(self, sha, **kwargs):
        for t in self.torrents:
            if t.sha == sha:
                for k, v in kwargs.items():
                    setattr(t, k, v)


class QuotaGuardTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(f"{tmp.name}/state.db")
        self.engine = FakeEngine()
        self.guard = QuotaGuard(self.engine, self.store, DailyQuota(100))  # 上限 100 字节

    def test_admit_starts_within_budget(self):
        decision = self.guard.admit(make_torrent(60, b"a1"), save_path="/x")
        self.assertTrue(decision.start)
        self.assertEqual(self.engine.torrents[0].state, TaskState.DOWNLOADING)

    def test_admit_queues_beyond_budget(self):
        self.guard.admit(make_torrent(60, b"a1"), save_path="/x")
        decision = self.guard.admit(make_torrent(50, b"a2"), save_path="/x")
        self.assertFalse(decision.start)
        self.assertEqual(self.engine.torrents[1].state, TaskState.PAUSED)

    def test_inflight_counts_against_budget(self):
        # 60 在途（未完成）时，预算只剩 40
        self.guard.admit(make_torrent(60, b"a1"), save_path="/x")
        decision = self.guard.admit(make_torrent(40, b"a2"), save_path="/x")
        self.assertTrue(decision.start)  # 恰好装下
        decision = self.guard.admit(make_torrent(41, b"a3"), save_path="/x")
        self.assertFalse(decision.start)

    def test_completion_frees_budget_and_drains_in_order(self):
        self.guard.admit(make_torrent(60, b"a1"), save_path="/x")
        self.guard.admit(make_torrent(30, b"a2"), save_path="/x")
        self.guard.admit(make_torrent(50, b"a3"), save_path="/x")  # 排队

        # a1 完成（60 字节已计入今日用量），a2 仍在途 30 → 剩余预算 10，放行不了
        t1, t2, t3 = self.engine.torrents
        t1.downloaded, t1.done, t1.state = 60, 60, TaskState.COMPLETED
        self.assertEqual(self.guard.drain(), [])
        self.assertEqual(t3.state, TaskState.PAUSED)

        # a2 也完成：今日已用 90，剩余预算 10，50 字节的 a3 仍放行不了
        t2.downloaded, t2.done, t2.state = 90, 90, TaskState.COMPLETED
        self.assertEqual(self.guard.drain(), [])
        self.assertEqual(t3.state, TaskState.PAUSED)

    def test_day_rollover_resets_budget_and_drains(self):
        self.guard.admit(make_torrent(90, b"a1"), save_path="/x")
        self.guard.admit(make_torrent(50, b"a2"), save_path="/x")  # 排队
        t1, t2 = self.engine.torrents
        t1.downloaded, t1.done, t1.state = 90, 90, TaskState.COMPLETED
        # 旧的一天先入账这 90 字节（真实场景里字节在当天就被轮询归集）
        self.assertEqual(self.guard.snapshot().used_today, 90)

        with mock.patch("server.services.quota_guard.time") as fake_time:
            fake_time.strftime.return_value = "2099-01-01"  # 新的一天
            resumed = self.guard.drain()
        self.assertEqual(resumed, [t2.sha])
        self.assertEqual(t2.state, TaskState.DOWNLOADING)

    def test_snapshot_numbers(self):
        snap = self.guard.snapshot()
        self.assertEqual(snap.limit, 100)
        self.assertEqual(snap.used_today, 0)
        self.assertEqual(snap.remaining, 100)

        self.guard.admit(make_torrent(40, b"a1"), save_path="/x")
        snap = self.guard.snapshot()
        self.assertEqual(snap.active_remaining, 40)  # 未完成，按全量计
        self.assertEqual(snap.remaining, 60)


if __name__ == "__main__":
    unittest.main()
