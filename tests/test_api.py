"""M2 API 端到端测试：FakeEngine + 临时库，走完整 HTTP 栈（TestClient）。"""

import tempfile
import unittest

from fastapi.testclient import TestClient

from mqd.quota import DailyQuota, GiB
from mqd.store import Store

from server.main import create_app
from server.services.app_context import AppContext
from server.services.mirrors import MirrorService
from server.services.quota_guard import QuotaGuard
from server.services.session_manager import SessionManager
from server.services.subscription_service import SubscriptionService

from fakes import FakeMikan, make_episode
from test_quota_guard import FakeEngine, make_torrent


class ApiTestBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        engine = FakeEngine()
        guard = QuotaGuard(
            engine,
            Store(f"{self.tmp}/state.db"),
            download_quota=DailyQuota(100),
            seed_limit_bytes=100,
        )
        self.mikan = FakeMikan()
        self.mirrors = MirrorService(guard.store, probe=lambda base: None)
        self.mirrors.ensure_seeded()
        self.ctx = AppContext(
            cfg={"mikan": {"base_url": "https://mikan.example"}},
            engine=engine,
            store=guard.store,
            guard=guard,
            sessions=SessionManager("https://mikan.example", f"{self.tmp}/session.json"),
            mikan=self.mikan,
            mirrors=self.mirrors,
        )
        self.ctx.subs = SubscriptionService(
            guard.store, guard, self.mikan, lambda: self.ctx.default_save_path,
            torrent_dir=f"{self.tmp}/torrents", mirrors=self.mirrors,
        )
        self.engine = engine
        self.client = TestClient(create_app(self.ctx))


class SystemApiTest(ApiTestBase):
    def test_health_reports_engine(self):
        data = self.client.get("/api/health").json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["engine"], "FakeEngine")

    def test_quota_snapshot_shape(self):
        data = self.client.get("/api/quota").json()
        self.assertEqual(data["download_limit"], 100)
        self.assertEqual(data["seed_limit"], 100)
        self.assertEqual(data["used_download"], 0)
        self.assertEqual(data["remaining"], 100)
        self.assertFalse(data["seed_gate_closed"])

    def test_update_limits_runtime(self):
        resp = self.client.post(
            "/api/quota/limits", json={"download_limit_gb": 2, "seed_limit_gb": 3}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["download_limit"], int(2 * GiB))
        self.assertEqual(data["seed_limit"], int(3 * GiB))

    def test_update_limits_rejects_garbage(self):
        resp = self.client.post("/api/quota/limits", json={"download_limit_gb": "abc"})
        self.assertEqual(resp.status_code, 422)


class TasksApiTest(ApiTestBase):
    def test_list_add_pause_resume_remove(self):
        sha = self.engine.add(make_torrent(40, b"a1"), paused=False, save_path="/x")
        tasks = self.client.get("/api/tasks").json()
        self.assertEqual([t["sha"] for t in tasks], [sha])
        self.assertEqual(tasks[0]["state"], "downloading")

        self.client.post(f"/api/tasks/{sha}/pause")
        self.assertEqual(self.engine.list()[0].state.value, "paused")

        self.client.post(f"/api/tasks/{sha}/resume")
        self.assertEqual(self.engine.list()[0].state.value, "downloading")

        self.client.delete(f"/api/tasks/{sha}")
        self.assertEqual(self.client.get("/api/tasks").json(), [])

    def test_operate_missing_task_returns_404(self):
        self.assertEqual(self.client.post("/api/tasks/deadbeef/pause").status_code, 404)
        self.assertEqual(self.client.delete("/api/tasks/deadbeef").status_code, 404)

    def test_rate_limit_endpoint(self):
        resp = self.client.post("/api/rate-limit", json={"down_bps": 2048, "up_bps": 1024})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.engine.download_bps, 2048)
        self.assertEqual(self.engine.upload_bps, 1024)


class SessionApiTest(ApiTestBase):
    def test_status_unconfigured_then_import(self):
        data = self.client.get("/api/session").json()
        self.assertFalse(data["configured"])

        resp = self.client.post(
            "/api/session/import", json={"cookie_string": "cf_clearance=abc; other=1"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["configured"])

        saved = self.ctx.sessions.load()
        names = {c["name"] for c in saved["storage_state"]["cookies"]}
        self.assertEqual(names, {"cf_clearance", "other"})

    def test_import_requires_nonempty(self):
        self.assertEqual(self.client.post("/api/session/import", json={}).status_code, 422)

    def test_clear_session(self):
        self.ctx.sessions.import_cookie_string("a=1")
        self.client.delete("/api/session")
        self.assertFalse(self.ctx.sessions.status()["configured"])

    def test_session_file_matches_v1_format(self):
        self.ctx.sessions.save("UA/1.0", {"cookies": [{"name": "n", "value": "v"}]})
        saved = self.ctx.sessions.load()
        self.assertEqual(saved["user_agent"], "UA/1.0")
        self.assertIn("storage_state", saved)


class SubscriptionsApiTest(ApiTestBase):
    URL = "https://mikan.example/RSS/Bangumi?bangumiId=3992&subgroupid=370"

    def setUp(self):
        super().setUp()
        self.ctx.default_save_path = "/dl"
        self.mikan.set_feed(
            self.URL, "某番", [make_episode("g1", "第01集", 1.0), make_episode("g2", "第02集", 2.0)]
        )

    def test_empty_list_with_interval(self):
        data = self.client.get("/api/subscriptions").json()
        self.assertEqual(data["subscriptions"], [])
        self.assertEqual(data["interval_minutes"], 20)

    def test_add_downloads_immediately_and_lists(self):
        resp = self.client.post("/api/subscriptions", json={"rss_url": self.URL, "title": "我的番"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["started"], 2)
        self.assertEqual(data["title"], "我的番")
        subs = self.client.get("/api/subscriptions").json()["subscriptions"]
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["title"], "我的番")

    def test_add_requires_directory(self):
        self.ctx.default_save_path = ""
        resp = self.client.post("/api/subscriptions", json={"rss_url": self.URL})
        self.assertEqual(resp.status_code, 422)
        self.assertIn("下载目录", resp.json()["detail"])

    def test_add_invalid_url_rejected(self):
        resp = self.client.post("/api/subscriptions", json={"rss_url": "junk"})
        self.assertEqual(resp.status_code, 422)

    def test_blocked_returns_guidance_but_saves(self):
        self.mikan.mode = "blocked"
        resp = self.client.post("/api/subscriptions", json={"rss_url": self.URL})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["blocked"])
        self.assertIn("Cookie", resp.json()["detail"])
        self.assertEqual(len(self.client.get("/api/subscriptions").json()["subscriptions"]), 1)

    def test_check_toggle_delete(self):
        sub_id = self.client.post("/api/subscriptions", json={"rss_url": self.URL}).json()["id"]
        result = self.client.post(f"/api/subscriptions/{sub_id}/check").json()
        self.assertEqual(result["started"], 0)  # GUID 去重

        toggled = self.client.post(
            f"/api/subscriptions/{sub_id}/toggle", json={"enabled": False}
        ).json()
        self.assertEqual(toggled["enabled"], 0)

        self.client.delete(f"/api/subscriptions/{sub_id}")  # 软删除
        data = self.client.get("/api/subscriptions").json()
        self.assertEqual(data["subscriptions"], [])
        self.assertEqual(len(data["deleted"]), 1)
        self.assertEqual(self.client.post(f"/api/subscriptions/{sub_id}/check").status_code, 404)

        restored = self.client.post(f"/api/subscriptions/{sub_id}/restore").json()
        self.assertEqual(restored["deleted_at"], 0)
        self.assertEqual(len(self.client.get("/api/subscriptions").json()["subscriptions"]), 1)

        self.client.delete(f"/api/subscriptions/{sub_id}?purge=true")  # 彻底删除
        data = self.client.get("/api/subscriptions").json()
        self.assertEqual(data["deleted"], [])
        self.assertIsNone(self.ctx.store.sub_get(sub_id))

    def test_check_all_endpoint(self):
        self.client.post("/api/subscriptions", json={"rss_url": self.URL})
        data = self.client.post("/api/subscriptions/check-all").json()
        self.assertEqual(data["checked"], 1)

    def test_interval_update(self):
        resp = self.client.post("/api/settings/interval", json={"minutes": 5})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            self.client.get("/api/subscriptions").json()["interval_minutes"], 5
        )
        self.assertEqual(
            self.client.post("/api/settings/interval", json={"minutes": 0}).status_code, 422
        )


class MirrorsApiTest(ApiTestBase):
    def test_seeded_defaults_present(self):
        hosts = [m["base_url"] for m in self.client.get("/api/mirrors").json()["mirrors"]]
        self.assertIn("https://mikan.tangbai.cc", hosts)
        self.assertIn("https://mikanime.tv", hosts)

    def test_add_normalizes_and_probes(self):
        resp = self.client.post("/api/mirrors", json={"base_url": "mikanani.kas.pub"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["base_url"], "https://mikanani.kas.pub")
        self.assertTrue(data["available"])  # 测试探针恒可用

    def test_add_invalid_rejected(self):
        self.assertEqual(
            self.client.post("/api/mirrors", json={"base_url": "   "}).status_code, 422
        )

    def test_check_and_remove(self):
        before = len(self.client.get("/api/mirrors").json()["mirrors"])
        summary = self.client.post("/api/mirrors/check").json()
        self.assertEqual(summary["checked"], before)
        self.assertEqual(summary["available"], before)
        mirror_id = self.client.get("/api/mirrors").json()["mirrors"][0]["id"]
        self.client.delete(f"/api/mirrors/{mirror_id}")
        self.assertEqual(len(self.client.get("/api/mirrors").json()["mirrors"]), before - 1)


if __name__ == "__main__":
    unittest.main()
