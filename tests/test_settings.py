"""设置 API 测试：默认下载目录的校验/应用/持久化，限额持久化。"""

import os
import tempfile
import unittest

import yaml

from mqd.quota import DailyQuota, GiB
from mqd.store import Store

from server.services.config_store import ConfigStore
from server.services.quota_guard import QuotaGuard
from server.services.session_manager import SessionManager

from test_api import ApiTestBase
from test_quota_guard import FakeEngine


class SettingsApiTest(ApiTestBase):
    def test_get_settings_defaults(self):
        data = self.client.get("/api/settings").json()
        self.assertEqual(data["engine"], "FakeEngine")
        self.assertEqual(data["save_path"], "")
        self.assertEqual(data["download_limit_gb"], 100 / GiB)
        self.assertEqual(data["seed_limit_gb"], 100 / GiB)
        self.assertFalse(data["config_persist"])

    def test_save_path_creates_dir_and_applies(self):
        target = os.path.join(self.tmp, "bangumi")
        resp = self.client.post("/api/settings/save-path", json={"save_path": target})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(os.path.isdir(target))  # 目录自动创建
        self.assertEqual(self.ctx.default_save_path, target)
        self.assertEqual(resp.json()["save_path"], target)

    def test_save_path_accepts_trailing_tilde_expansion(self):
        resp = self.client.post("/api/settings/save-path", json={"save_path": "~/mqd-test-xxx"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["save_path"].startswith("/"))
        os.rmdir(os.path.expanduser("~/mqd-test-xxx"))

    def test_save_path_rejects_relative(self):
        resp = self.client.post("/api/settings/save-path", json={"save_path": "relative/dir"})
        self.assertEqual(resp.status_code, 422)

    def test_save_path_rejects_empty(self):
        resp = self.client.post("/api/settings/save-path", json={"save_path": "  "})
        self.assertEqual(resp.status_code, 422)


class ConfigPersistenceTest(unittest.TestCase):
    """回写必须落在实际加载的 config.yaml 上；未配置路径时为 no-op。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.config_path = os.path.join(self.tmp, "config.yaml")
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write("quota:\n  daily_limit_gb: 30\nmikan:\n  base_url: https://x\n")

        self.engine = FakeEngine()
        self.ctx = self._build_context()
        self.client = self._client()

    def _build_context(self):
        from server.services.app_context import AppContext

        return AppContext(
            cfg={"mikan": {"base_url": "https://mikan.example"}},
            engine=self.engine,
            store=Store(f"{self.tmp}/state.db"),
            guard=QuotaGuard(self.engine, Store(f"{self.tmp}/state.db"), download_quota=DailyQuota(100)),
            sessions=SessionManager("https://mikan.example", f"{self.tmp}/session.json"),
            config_store=ConfigStore(self.config_path),
        )

    def _client(self):
        from fastapi.testclient import TestClient

        from server.main import create_app

        return TestClient(create_app(self.ctx))

    def _yaml(self):
        with open(self.config_path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def test_save_path_persists_to_config(self):
        target = os.path.join(self.tmp, "dl")
        resp = self.client.post("/api/settings/save-path", json={"save_path": target})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._yaml()["desktop"]["save_path"], target)

    def test_update_limits_persists_to_config(self):
        resp = self.client.post("/api/quota/limits", json={"download_limit_gb": 2, "seed_limit_gb": 3})
        self.assertEqual(resp.status_code, 200)
        data = self._yaml()
        self.assertEqual(data["quota"]["daily_limit_gb"], 2)
        self.assertEqual(data["quota"]["seed_limit_gb"], 3)
        # 其他小节不被破坏
        self.assertEqual(data["mikan"]["base_url"], "https://x")

    def test_without_config_store_no_writes(self):
        """未提供 config_store（测试/特殊部署）时，保存目录仅作用于运行时。"""
        self.ctx.config_store = None
        resp = self.client.post("/api/settings/save-path", json={"save_path": "/tmp/mqd-x"})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("desktop", self._yaml())


if __name__ == "__main__":
    unittest.main()
