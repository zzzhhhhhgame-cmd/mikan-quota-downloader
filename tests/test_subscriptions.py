"""订阅（RSS 链接）服务测试：粘贴即下载、去重、限额排队、CF 拦截、目录校验。"""

import tempfile
import unittest

from mqd.mikan import Episode
from mqd.quota import DailyQuota
from mqd.session import CloudflareBlocked
from mqd.store import Store

from mqd.torrents import torrent_name
from server.engine.base import TaskState
from server.services.mirrors import MirrorService
from server.services.quota_guard import QuotaGuard
from server.services.subscription_service import SubscriptionError, SubscriptionService

from fakes import FakeMikan, make_episode
from test_quota_guard import FakeEngine

URL = "https://mikan.example/RSS/Bangumi?bangumiId=3992&subgroupid=370"


class SubscriptionServiceTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.paths = {"default": "/dl"}
        self.engine = FakeEngine()
        self.store = Store(f"{self.tmp}/state.db")
        self.guard = QuotaGuard(self.engine, self.store, download_quota=DailyQuota(100), seed_limit_bytes=100)
        self.mikan = FakeMikan()
        self.mirrors = MirrorService(self.store, probe=lambda base: None)
        self.mirrors.ensure_seeded()
        self.subs = SubscriptionService(
            self.store, self.guard, self.mikan,
            save_path_provider=lambda: self.paths["default"],
            torrent_dir=f"{self.tmp}/torrents",
            mirrors=self.mirrors,
        )

    def test_add_downloads_immediately(self):
        self.mikan.set_feed(URL, "Mikan Project - 尼古喵喵", [make_episode("g1", "第1集", 1.0), make_episode("g2", "第2集", 2.0)])
        summary = self.subs.add(URL)
        self.assertEqual(summary["title"], "尼古喵喵")  # 自动去站点前缀
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["started"], 2)
        self.assertEqual(len(self.engine.torrents), 2)
        # 每番独立目录：<默认下载目录>/<番剧名>
        self.assertTrue(all(t.save_path == "/dl/尼古喵喵" for t in self.engine.torrents))
        self.assertEqual(self.store.sub_get(summary["id"])["save_path"], "/dl/尼古喵喵")

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

    def test_organize_fills_dir_and_moves_files(self):
        """整理：旧订阅补目录 + 引擎任务文件搬进番剧文件夹。"""
        self.paths["default"] = "/dl"
        self.mikan.set_feed(URL, "Mikan Project - 尼古喵喵", [make_episode("g1", published=1.0)])
        self.mikan.sizes = {"g1": 60}
        sub_id = self.subs.add(URL)["id"]
        # 模拟旧数据：任务还在旧目录、订阅目录未设置
        self.store.sub_set_save_path(sub_id, "")
        self.engine._set(self.engine.torrents[0].sha, save_path="/old")

        result = self.subs.organize()
        self.assertEqual(result["paths_set"], 1)
        self.assertEqual(result["moved"], 1)
        sub = self.store.sub_get(sub_id)
        self.assertEqual(sub["save_path"], "/dl/尼古喵喵")
        self.assertEqual(self.engine.torrents[0].save_path, "/dl/尼古喵喵")
        self.assertIn((self.engine.torrents[0].sha, "/dl/尼古喵喵"), self.engine.moved)

    def test_organize_fetches_missing_title(self):
        self.paths["default"] = "/dl"
        # 订阅存的是域名无关路径，镜像列表里 tangbai.cc 可用 → 从那里拉标题
        path = "/RSS/Bangumi?bangumiId=3992&subgroupid=370"
        self.mikan.set_feed("https://mikan.tangbai.cc" + path, "Mikan Project - 尼古喵喵", [])
        sub_id = self.store.sub_add(path, "", "")  # 旧订阅：无标题
        result = self.subs.organize(fetch_titles=True)
        self.assertEqual(result["renamed"], 1)
        self.assertEqual(self.store.sub_get(sub_id)["title"], "尼古喵喵")
        self.assertEqual(self.store.sub_get(sub_id)["save_path"], "/dl/尼古喵喵")

    def test_check_lazy_fills_title_and_dir(self):
        """旧订阅（无标题无目录）检查一次后自动补全。"""
        path = "/RSS/Bangumi?bangumiId=7&subgroupid=1"
        self.mikan.set_feed("https://mikan.tangbai.cc" + path, "Mikan Project - 懒加载番", [make_episode("g9", published=1.0)])
        sub_id = self.store.sub_add(path)
        self.subs.check_one(sub_id)
        sub = self.store.sub_get(sub_id)
        self.assertEqual(sub["title"], "懒加载番")
        self.assertEqual(sub["save_path"], "/dl/懒加载番")

    def test_safe_folder_sanitizes_illegal_chars(self):
        self.assertEqual(SubscriptionService._safe_folder('Re:Zero / "Second" <Part> |2?'), "Re Zero Second Part 2")
        self.assertEqual(SubscriptionService._safe_folder("  "), "未命名番剧")

    def test_clean_title_strips_site_prefix(self):
        clean = SubscriptionService._clean_title
        self.assertEqual(clean("Mikan Project - 尼古喵喵"), "尼古喵喵")
        self.assertEqual(clean("mikan project - 尼古喵喵"), "尼古喵喵")
        self.assertEqual(clean("蜜柑计划 - 孤独摇滚"), "孤独摇滚")
        self.assertEqual(clean("孤独摇滚"), "孤独摇滚")

    def test_organize_relocates_lost_loose_files(self):
        """引擎里已丢失的集数：按存档内容名把散落在下载目录根的文件搬进番剧文件夹。"""
        import os as _os

        dl = _os.path.join(self.tmp, "dl")
        self.paths["default"] = dl
        self.mikan.set_feed(URL, "Mikan Project - 尼古喵喵", [make_episode("g1", published=1.0)])
        self.mikan.sizes = {"g1": 60}
        sub_id = self.subs.add(URL)["id"]
        sha = self.store.episode_pending(sub_id)[0]["sha"]
        self.engine.remove(sha)  # 任务丢失
        # 下载目录根下有散落的同名文件（单文件种子落盘名 = name + 扩展名）
        raw = self.mikan.download_torrent(make_episode("g1"))
        loose = _os.path.join(dl, torrent_name(raw) + ".mkv")
        _os.makedirs(dl, exist_ok=True)
        with open(loose, "wb") as f:
            f.write(b"x" * 60)

        result = self.subs.organize()  # 离线整理（引擎为空也要能搬文件）
        self.assertGreaterEqual(result["files_moved"], 1)
        self.assertFalse(_os.path.exists(loose))  # 散落文件已归位
        moved_into = _os.path.join(dl, "尼古喵喵", torrent_name(raw) + ".mkv")
        self.assertTrue(_os.path.isfile(moved_into))

    def test_organize_renames_folder_after_rename(self):
        import os as _os

        dl = _os.path.join(self.tmp, "dl")
        self.paths["default"] = dl
        self.mikan.set_feed(URL, "Mikan Project - 尼古喵喵", [])
        sub_id = self.subs.add(URL)["id"]
        sub = self.store.sub_get(sub_id)
        old_dir = sub["save_path"]
        _os.makedirs(old_dir, exist_ok=True)
        with open(_os.path.join(old_dir, "a.txt"), "w") as f:
            f.write("x")

        self.subs.rename(sub_id, "新名字")
        result = self.subs.organize()
        self.assertEqual(result["dirs_renamed"], 1)
        self.assertTrue(_os.path.isdir(_os.path.join(dl, "新名字")))
        self.assertTrue(_os.path.isfile(_os.path.join(dl, "新名字", "a.txt")))
        self.assertFalse(_os.path.exists(old_dir))

    def test_legacy_seen_episode_still_downloads(self):
        """第 10 集卡死问题的回归测试：老订阅只有 seen 标记也必须能下载。"""
        path = "/RSS/Bangumi?bangumiId=3992&subgroupid=370"
        self.mikan.set_feed("https://mikanime.tv" + path, "Mikan Project - 尼古喵喵",
                            [make_episode("g10", published=10.0)])
        self.mikan.sizes = {"g10": 60}
        self.store.mark_seen("g10")  # 旧版遗留：已标记 seen 但任务早已丢失
        sub_id = self.store.sub_add(path, "尼古喵喵", "/dl/尼古喵喵", "mikanime.tv")
        result = self.subs.check_one(sub_id)
        self.assertEqual(result["started"], 1)  # 不再被 seen 挡住
        self.assertEqual(len(self.engine.torrents), 1)

    def test_reconcile_restores_done_episode_for_seeding(self):
        """重启后做种恢复：已完成过的集数任务丢失 → 从存档重新入队做种。"""
        self.mikan.set_feed(URL, "某番", [make_episode("g1", published=1.0)])
        self.mikan.sizes = {"g1": 60}
        sub_id = self.subs.add(URL)["id"]
        sha = self.store.episode_pending(sub_id)[0]["sha"]
        self.store.episode_mark_done("g1")
        self.store.mark_seen("g1")
        self.engine.remove(sha)  # 模拟重启后任务丢失
        self.assertEqual(len(self.engine.list()), 0)

        self.subs.reconcile(self.store.sub_get(sub_id))
        self.assertEqual(len(self.engine.list()), 1)  # 重新入队做种

    def test_sealed_episode_never_restored(self):
        self.mikan.set_feed(URL, "某番", [make_episode("g1", published=1.0)])
        sub_id = self.subs.add(URL)["id"]
        sha = self.store.episode_pending(sub_id)[0]["sha"]
        self.engine.remove(sha)
        self.store.episode_seal_by_sha(sha)  # 用户手动删除过 → 封存
        self.subs.reconcile(self.store.sub_get(sub_id))
        self.assertEqual(len(self.engine.list()), 0)  # 不恢复

    def test_download_failover_rewrites_host(self):
        """当前镜像下载种子失败 → 自动换下一个镜像重试。"""
        ep = make_episode("g1")
        self.mikan.dead_hosts = {"mikan.example"}  # 条目所在域名下载失败
        raw = self.subs._download_with_failover(ep, preferred_host="mikan.example")
        self.assertTrue(raw.startswith(b"d"))  # 换镜像后成功拿到种子

    def test_download_failover_all_dead_raises(self):
        self.mikan.set_feed(URL, "某番", [make_episode("g1", published=1.0)])
        ep = self.subs  # 占位
        e = __import__("mqd.mikan", fromlist=["Episode"]).Episode(
            guid="g9", title="t", page_url="https://mikan.example/Home/Episode/g9", published=0)
        self.mikan.dead_hosts = {"mikan.tangbai.cc", "mikanime.tv", "mikanani.me",
                                 "mikan.sakiko.de", "mikanani.kas.pub", "mikan.example"}
        with self.assertRaises(RuntimeError):
            self.subs._download_with_failover(e, preferred_host="mikan.example")

    def test_purge_removes_tracking(self):
        self.mikan.set_feed(URL, "某番", [])
        sub_id = self.subs.add(URL)["id"]
        self.store.sub_purge(sub_id)
        self.assertIsNone(self.store.sub_get(sub_id))

    def test_domain_replaced_with_working_mirror(self):
        """粘贴被墙域名的 RSS → 自动换到可用镜像订阅，存储的是域名无关路径。"""
        self.mikan.dead_hosts = {"mikan.tangbai.cc"}  # 种子列表里第一个域名挂了
        path = "/RSS/Bangumi?bangumiId=1&subgroupid=2"
        self.mikan.set_feed("https://mikanime.tv" + path, "某番", [make_episode("g1", published=1.0)])
        summary = self.subs.add("https://mikan.tangbai.cc" + path)
        self.assertEqual(summary["started"], 1)
        sub = self.store.sub_get(summary["id"])
        self.assertEqual(sub["rss_url"], path)  # 存的是路径，与域名无关
        self.assertEqual(sub["mirror_host"], "mikanime.tv")  # 记住了可用镜像

    def test_check_rotates_when_preferred_mirror_dies(self):
        path = "/RSS/Bangumi?bangumiId=1&subgroupid=2"
        self.mikan.set_feed("https://mikanime.tv" + path, "某番", [make_episode("g1", published=1.0)])
        summary = self.subs.add("https://mikanime.tv" + path)
        sub_id = summary["id"]
        self.assertEqual(self.store.sub_get(sub_id)["mirror_host"], "mikanime.tv")

        # mikanime 挂了，但 tangbai 上同路径可用 → 自动切换
        self.mikan.dead_hosts.add("mikanime.tv")
        self.mikan.set_feed("https://mikan.tangbai.cc" + path, "某番", [make_episode("g1", published=1.0)])
        result = self.subs.check_one(sub_id)
        self.assertEqual(result["started"], 0)  # g1 已完成去重，但拉取成功
        self.assertEqual(self.store.sub_get(sub_id)["mirror_host"], "mikan.tangbai.cc")

    def test_all_mirrors_dead_raises(self):
        self.mikan.dead_hosts = {"mikan.tangbai.cc", "mikanime.tv", "mikanani.me",
                                 "mikan.sakiko.de", "mikanani.kas.pub", "mikan.example"}
        path = "/RSS/Bangumi?bangumiId=1&subgroupid=2"
        with self.assertRaises(SubscriptionError):
            self.subs.add("https://mikan.tangbai.cc" + path)

    def test_disabled_subscription_skipped_in_check_all(self):
        self.mikan.set_feed(URL, "某番", [make_episode("g1", published=1.0)])
        sub_id = self.subs.add(URL)["id"]
        self.store.sub_set_enabled(sub_id, False)
        result = self.subs.check_all()
        self.assertEqual(result["checked"], 0)

    def test_check_all_stops_on_block(self):
        self.mikan.set_feed("https://x/RSS/1", "a", [])
        self.mikan.set_feed("https://x/RSS/2", "b", [])
        self.subs.add("https://x/RSS/1")
        self.subs.add("https://x/RSS/2")
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
