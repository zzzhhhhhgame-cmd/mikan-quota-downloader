"""订阅（RSS 链接）服务测试：粘贴即下载、去重、限额排队、CF 拦截、目录校验。"""

import tempfile
import unittest

from mqd.mikan import Episode
from mqd.quota import DailyQuota
from mqd.session import CloudflareBlocked
from mqd.store import Store

from server.engine.base import TaskState
from server.services.quota_guard import QuotaGuard
from server.services.subscription_service import SubscriptionError, SubscriptionService

from fakes import FakeMikan, make_episode
from test_quota_guard import FakeEngine

URL = "https://mikan.example/RSS/Bangumi?bangumiId=3992&subgroupid=370"


class SubscriptionServiceTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paths = {"default": "/dl"}
        self.engine = FakeEngine()
        self.store = Store(f"{tmp.name}/state.db")
        self.guard = QuotaGuard(self.engine, self.store, download_quota=DailyQuota(100), seed_limit_bytes=100)
        self.mikan = FakeMikan()
        self.subs = SubscriptionService(
            self.store, self.guard, self.mikan,
            save_path_provider=lambda: self.paths["default"],
            torrent_dir=f"{tmp.name}/torrents",
        )

    def test_add_downloads_immediately(self):
        self.mikan.set_feed(URL, "某番", [make_episode("g1", "第1集", 1.0), make_episode("g2", "第2集", 2.0)])
        summary = self.subs.add(URL)
        self.assertEqual(summary["title"], "某番")  # 未填名称时自动取订阅源标题
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["started"], 2)
        self.assertEqual(len(self.engine.torrents), 2)
        # 所有任务都落到默认目录
        self.assertTrue(all(t.save_path == "/dl" for t in self.engine.torrents))

    def test_recheck_dedups_by_guid(self):
        self.mikan.set_feed(URL, "某番", [make_episode("g1", published=1.0)])
        summary = self.subs.add(URL)
        self.assertEqual(summary["started"], 1)
        again = self.subs.check_one(summary["id"])
        self.assertEqual(again["started"], 0)  # 源滚动但内容没变，不重复下载
        self.assertEqual(len(self.engine.torrents), 1)
        # 新集出现 → 下一轮检查自动下载
        episodes = self.mikan.feeds[URL][1] + [make_episode("g2", published=2.0)]
        self.mikan.set_feed(URL, "某番", episodes)
        third = self.subs.check_one(summary["id"])
        self.assertEqual(third["started"], 1)
        self.assertEqual(len(self.engine.torrents), 2)

    def test_quota_queueing_across_checks(self):
        self.mikan.set_feed(URL, "某番", [make_episode("g1", published=1.0)])
        self.mikan.sizes = {"g1": 60}
        summary = self.subs.add(URL)
        self.assertEqual(summary["started"], 1)
        episodes = self.mikan.feeds[URL][1] + [make_episode("g2", published=2.0)]
        self.mikan.set_feed(URL, "某番", episodes)
        self.mikan.sizes["g2"] = 50  # 剩余预算 40，装不下 → 排队
        result = self.subs.check_one(summary["id"])
        self.assertEqual(result["queued"], 1)
        self.assertEqual(self.engine.torrents[1].state.value, "paused")

    def test_blocked_still_saves_subscription(self):
        self.mikan.mode = "blocked"
        summary = self.subs.add(URL)
        self.assertTrue(summary["blocked"])
        self.assertIn("Cookie", summary["detail"])
        self.assertEqual(len(self.store.sub_list()), 1)  # 已保存，Cookie 导入后自动恢复
        self.mikan.mode = "ok"
        self.mikan.set_feed(URL, "某番", [make_episode("g1", published=1.0)])
        recovered = self.subs.check_one(summary["id"])
        self.assertEqual(recovered["started"], 1)

    def test_per_subscription_save_path_override(self):
        self.paths["default"] = ""  # 未设默认目录
        self.mikan.set_feed(URL, "某番", [make_episode("g1", published=1.0)])
        summary = self.subs.add(URL, save_path="/custom")
        self.assertEqual(summary["started"], 1)
        self.assertEqual(self.engine.torrents[0].save_path, "/custom")

    def test_requires_directory_when_fresh(self):
        self.paths["default"] = ""
        self.mikan.set_feed(URL, "某番", [make_episode("g1", published=1.0)])
        with self.assertRaises(SubscriptionError):
            self.subs.add(URL)
        # 无新条目时不要求目录
        self.mikan.set_feed("https://mikan.example/RSS/empty", "空源", [])
        self.subs.add("https://mikan.example/RSS/empty")

    def test_upsert_same_url(self):
        self.mikan.set_feed(URL, "某番", [])
        first = self.subs.add(URL, title="A")
        second = self.subs.add(URL, title="B")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.store.sub_list()), 1)
        self.assertEqual(self.store.sub_get(first["id"])["title"], "B")

    def test_invalid_url_rejected(self):
        with self.assertRaises(SubscriptionError):
            self.subs.add("不是链接")

    def test_toggle_and_soft_delete(self):
        self.mikan.set_feed(URL, "某番", [])
        sub_id = self.subs.add(URL)["id"]
        self.store.sub_set_enabled(sub_id, False)
        self.assertEqual(self.store.sub_list(enabled_only=True), [])
        self.subs.delete(sub_id)  # 软删除
        self.assertEqual(self.store.sub_list(), [])
        self.assertEqual(len(self.store.sub_list(deleted=True)), 1)
        self.subs.restore(sub_id)
        self.assertEqual(len(self.store.sub_list()), 1)

    def test_episode_tracked_until_done(self):
        """集数在下载完成前不算「已见」；完成后才写入 seen 永久去重。"""
        self.mikan.set_feed(URL, "某番", [make_episode("g1", published=1.0)])
        self.mikan.sizes = {"g1": 60}
        summary = self.subs.add(URL)
        self.assertEqual(summary["started"], 1)
        self.assertFalse(self.store.seen("g1"))  # 未完成，不算已见
        pending = self.store.episode_pending(summary["id"])
        self.assertEqual(len(pending), 1)
        sha = pending[0]["sha"]
        # 任务完成（做种中）→ reconcile 标记 done
        self.engine._set(sha, state=TaskState.SEEDING, done=60)
        self.subs.reconcile(self.store.sub_get(summary["id"]))
        self.assertTrue(self.store.seen("g1"))
        self.assertEqual(self.store.episode_pending(summary["id"]), [])

    def test_reconcile_readds_lost_task_from_archive(self):
        """任务在引擎里丢失（手动删除/重启）→ 从本地 .torrent 存档自动重新入队。"""
        self.mikan.set_feed(URL, "某番", [make_episode("g1", published=1.0)])
        self.mikan.sizes = {"g1": 60}
        sub_id = self.subs.add(URL)["id"]
        sha = self.store.episode_pending(sub_id)[0]["sha"]
        self.engine.remove(sha)  # 模拟任务丢失
        self.assertEqual(len(self.engine.list()), 0)
        readded = self.subs.reconcile(self.store.sub_get(sub_id))
        self.assertEqual(readded, 1)
        self.assertEqual(len(self.engine.list()), 1)
        self.assertEqual(self.engine.list()[0].sha, sha)  # 相同 infohash，续传既有文件
        # 再跑一次不应重复入队
        self.assertEqual(self.subs.reconcile(self.store.sub_get(sub_id)), 0)
        self.assertEqual(len(self.engine.list()), 1)

    def test_purge_removes_tracking(self):
        self.mikan.set_feed(URL, "某番", [])
        sub_id = self.subs.add(URL)["id"]
        self.store.sub_purge(sub_id)
        self.assertIsNone(self.store.sub_get(sub_id))

    def test_disabled_subscription_skipped_in_check_all(self):
        self.mikan.set_feed(URL, "某番", [make_episode("g1", published=1.0)])
        sub_id = self.subs.add(URL)["id"]
        self.store.sub_set_enabled(sub_id, False)
        result = self.subs.check_all()
        self.assertEqual(result["checked"], 0)

    def test_check_all_stops_on_block(self):
        self.mikan.set_feed("https://x/1", "a", [])
        self.mikan.set_feed("https://x/2", "b", [])
        self.subs.add("https://x/1")
        self.subs.add("https://x/2")
        self.mikan.mode = "blocked"
        result = self.subs.check_all()
        self.assertEqual(result["blocked"], 1)
        self.assertEqual(result["checked"], 1)  # 拦截后停止本轮，不再触发第二个源

    def test_item_error_does_not_block_others(self):
        self.mikan.set_feed(URL, "某番", [make_episode("bad", published=1.0), make_episode("good", published=2.0)])
        original = self.mikan.download_torrent

        def flaky(episode):
            if episode.guid == "bad":
                raise RuntimeError("页面解析失败")
            return original(episode)

        self.mikan.download_torrent = flaky
        summary = self.subs.add(URL)
        self.assertEqual(len(summary["errors"]), 1)
        self.assertEqual(summary["started"], 1)  # 坏条目不拖累好条目


if __name__ == "__main__":
    unittest.main()
