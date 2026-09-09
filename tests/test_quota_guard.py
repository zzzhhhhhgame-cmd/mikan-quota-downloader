"""QuotaGuard 单测：限额判定、排队与按序放行、跨天重置。"""

import tempfile
import time
import unittest
from unittest import mock

from mqd.quota import DailyQuota, GiB
from mqd.store import Store
from mqd.torrents import infohash_from_bytes, torrent_size

from server.engine.base import Engine, EngineError, TaskState, TorrentState
from server.services.quota_guard import GATE_BPS, QuotaGuard


def make_torrent(length: int, tag: bytes = b"a1") -> bytes:
    """构造单文件 bencode 种子（tag 恒 2 字节，保证编码合法）。"""
    return b"d4:infod6:lengthi" + str(length).encode() + b"e4:name2:" + tag + b"ee"


class FakeEngine(Engine):
    """内存引擎：只维护状态机，供限额层测试。"""

    def __init__(self):
        self.torrents = []
        self.resumed = []
        self.download_bps = None
        self.upload_bps = None
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

    def add_magnet(self, uri: str, *, paused: bool, save_path: str, sequential: bool = False, category: str = "") -> str:
        from mqd.torrents import magnet_infohash

        sha = magnet_infohash(uri)
        if any(t.sha == sha for t in self.torrents):
            return sha
        self._seq += 1
        self.torrents.append(
            TorrentState(
                sha=sha,
                name=uri[:48],
                state=TaskState.PAUSED if paused else TaskState.DOWNLOADING,
                size=0,  # 磁链元数据到达前体积未知
                save_path=save_path,
                sequential=sequential,
                added_at=time.time() + self._seq / 1000.0,
                order=self._seq,
            )
        )
        return sha

    def pause(self, sha):
        self._require(sha)
        self._set(sha, state=TaskState.PAUSED)

    def resume(self, sha):
        self._require(sha)
        self.resumed.append(sha)
        self._set(sha, state=TaskState.DOWNLOADING)

    def remove(self, sha, with_files=False):
        self._require(sha)
        self.torrents = [t for t in self.torrents if t.sha != sha]

    def list(self):
        return list(self.torrents)

    def set_global_limit(self, down_bps):
        pass

    def set_download_limit(self, down_bps):
        self.download_bps = down_bps

    def set_upload_limit(self, up_bps):
        self.upload_bps = up_bps

    def _require(self, sha):
        if all(t.sha != sha for t in self.torrents):
            raise EngineError(f"任务不存在: {sha}")

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
        # 测试期双限额均为 1GB 的等比缩小版：下载 100 字节、做种 100 字节
        self.guard = QuotaGuard(
            self.engine,
            self.store,
            download_quota=DailyQuota(100),
            seed_limit_bytes=100,
        )

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
        self.assertEqual(self.guard.snapshot().used_download, 90)

        with mock.patch("server.services.quota_guard.time") as fake_time:
            fake_time.strftime.return_value = "2099-01-01"  # 新的一天
            resumed = self.guard.drain()
        self.assertEqual(resumed, [t2.sha])
        self.assertEqual(t2.state, TaskState.DOWNLOADING)

    def test_snapshot_numbers(self):
        snap = self.guard.snapshot()
        self.assertEqual(snap.download_limit, 100)
        self.assertEqual(snap.used_download, 0)
        self.assertEqual(snap.used_upload, 0)
        self.assertEqual(snap.remaining, 100)
        self.assertFalse(snap.seed_gate_closed)

        self.guard.admit(make_torrent(40, b"a1"), save_path="/x")
        snap = self.guard.snapshot()
        self.assertEqual(snap.active_remaining, 40)  # 未完成，按全量计
        self.assertEqual(snap.remaining, 60)

    def test_seed_gate_closes_when_quota_reached(self):
        self.guard.admit(make_torrent(60, b"a1"), save_path="/x")
        t1 = self.engine.torrents[0]
        t1.uploaded = 100  # 触达做种上限 100 字节
        self.assertTrue(self.guard.apply_seed_policy())
        self.assertEqual(self.engine.upload_bps, GATE_BPS)

        # 次日重置 → 闸门放开，还原用户上传限速设定（此处为不限）
        with mock.patch("server.services.quota_guard.time") as fake_time:
            fake_time.strftime.return_value = "2099-01-01"
            self.assertFalse(self.guard.apply_seed_policy())
        self.assertIsNone(self.engine.upload_bps)

    def test_seed_gate_restores_user_upload_rate(self):
        guard = QuotaGuard(
            self.engine,
            self.store,
            download_quota=DailyQuota(100),
            seed_limit_bytes=100,
            user_upload_bps=512,
        )
        self.engine.torrents.append(
            TorrentState(sha="x", name="x", state=TaskState.DOWNLOADING, uploaded=999)
        )
        self.assertTrue(guard.apply_seed_policy())
        self.assertEqual(self.engine.upload_bps, GATE_BPS)

        # 次日（上传账目重置）→ 闸门放开，还原用户上传限速 512
        with mock.patch("server.services.quota_guard.time") as fake_time:
            fake_time.strftime.return_value = "2099-01-01"
            self.assertFalse(guard.apply_seed_policy())
        self.assertEqual(self.engine.upload_bps, 512)

    def test_seed_limit_disabled_never_closes(self):
        self.guard = QuotaGuard(self.engine, self.store, download_quota=DailyQuota(100))
        self.engine.torrents.append(
            TorrentState(sha="x", name="x", state=TaskState.DOWNLOADING, uploaded=10**9)
        )
        self.assertFalse(self.guard.apply_seed_policy())
        self.assertIsNone(self.engine.upload_bps)

    def test_sync_returns_drain_and_gate(self):
        self.guard.admit(make_torrent(60, b"a1"), save_path="/x")
        resumed, gate_closed = self.guard.sync()
        self.assertEqual(resumed, [])
        self.assertFalse(gate_closed)

    def test_drain_pauses_oversized_zero_progress_task(self):
        """磁链元数据后置：体积已知、尚未花流量且超预算 → 自动暂停转入等待队列。"""
        self.engine.torrents.append(
            TorrentState(sha="big", name="big", state=TaskState.DOWNLOADING, size=200, downloaded=0)
        )
        self.guard.drain()
        self.assertEqual(self.engine.torrents[0].state, TaskState.PAUSED)

    def test_drain_spends_started_task_alone(self):
        """已经开始花流量的任务不强制暂停（让它跑完或由用户处理）。"""
        self.engine.torrents.append(
            TorrentState(sha="mid", name="mid", state=TaskState.DOWNLOADING, size=200, downloaded=30)
        )
        self.guard.drain()
        self.assertEqual(self.engine.torrents[0].state, TaskState.DOWNLOADING)

    def test_seeding_excluded_from_active_budget(self):
        self.engine.torrents.append(
            TorrentState(sha="s1", name="s1", state=TaskState.SEEDING, size=500, done=500)
        )
        snap = self.guard.snapshot()
        self.assertEqual(snap.active_remaining, 0)
        self.assertEqual(snap.remaining, 100)


if __name__ == "__main__":
    unittest.main()
