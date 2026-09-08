"""M2 API 端到端测试：FakeEngine + 临时库，走完整 HTTP 栈（TestClient）。"""

import tempfile
import unittest

from fastapi.testclient import TestClient

from mqd.quota import DailyQuota, GiB
from mqd.store import Store

from server.main import create_app
from server.services.app_context import AppContext
from server.services.quota_guard import QuotaGuard
from server.services.session_manager import SessionManager

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
        self.ctx = AppContext(
            cfg={"mikan": {"base_url": "https://mikan.example"}},
            engine=engine,
            store=guard.store,
            guard=guard,
            sessions=SessionManager("https://mikan.example", f"{self.tmp}/session.json"),
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

    def test_harvest_without_desktop_bridge_returns_501(self):
        resp = self.client.post("/api/session/harvest")
        self.assertEqual(resp.status_code, 501)

    def test_harvest_with_bridge_callback(self):
        self.ctx.harvest_callback = lambda: {"ok": True, "cookies": 3}
        resp = self.client.post("/api/session/harvest")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["cookies"], 3)

    def test_session_file_matches_v1_format(self):
        self.ctx.sessions.save("UA/1.0", {"cookies": [{"name": "n", "value": "v"}]})
        saved = self.ctx.sessions.load()
        self.assertEqual(saved["user_agent"], "UA/1.0")
        self.assertIn("storage_state", saved)


if __name__ == "__main__":
    unittest.main()
