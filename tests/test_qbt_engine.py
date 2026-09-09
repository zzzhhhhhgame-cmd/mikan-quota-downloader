"""QbtWebuiEngine 单测：起一个本地 mock qBittorrent Web API 服务做黑盒验证。"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mqd.torrents import infohash_from_bytes

from server.engine.base import Engine, EngineError, TaskState
from server.engine.qbt_engine import QbtWebuiEngine

TORRENT = b"d4:infod6:lengthi1000e4:name2:teee"


def make_mock_qbt(captured):
    outer = captured

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code=200, body=b"Ok.", ctype="text/plain"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length)
            outer["requests"].append((self.path, raw))
            if self.path == "/api/v2/auth/login":
                self._send(body=b"Ok.")
            elif self.path in (
                "/api/v2/torrents/add",
                "/api/v2/torrents/resume",
                "/api/v2/torrents/start",
                "/api/v2/torrents/pause",
                "/api/v2/torrents/stop",
                "/api/v2/torrents/delete",
                "/api/v2/transfer/setDownloadLimit",
                "/api/v2/transfer/setUploadLimit",
                "/api/v2/app/setPreferences",
            ):
                self._send()
            else:
                self._send(404, b"")

        def do_GET(self):
            if self.path.startswith("/api/v2/torrents/info"):
                self._send(body=json.dumps(outer["torrents"]).encode(), ctype="application/json")
            else:
                self._send(404, b"")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class QbtEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.captured = {"torrents": [], "requests": []}
        cls.server = make_mock_qbt(cls.captured)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.engine = QbtWebuiEngine(self.url, "admin", "pw")
        self.engine.start()
        self.captured["requests"].clear()
        self.captured["torrents"].clear()

    def test_conforms_to_engine_interface(self):
        self.assertIsInstance(self.engine, Engine)

    def test_add_returns_infohash_and_posts_torrent(self):
        sha = self.engine.add(TORRENT, paused=True, save_path="/downloads")
        self.assertEqual(sha, infohash_from_bytes(TORRENT))
        posts = [raw for path, raw in self.captured["requests"] if path == "/api/v2/torrents/add"]
        self.assertEqual(len(posts), 1)
        self.assertIn(b'name="paused"', posts[0])  # 4.x 参数
        self.assertIn(b'name="stopped"', posts[0])  # 5.x 参数
        self.assertIn(b"name=\"savepath\"", posts[0])
        self.assertIn(b"d4:info", posts[0])  # 种子本体已上传

    def test_add_is_idempotent(self):
        self.captured["torrents"].append({"hash": infohash_from_bytes(TORRENT), "state": "pausedDL"})
        sha = self.engine.add(TORRENT, paused=True, save_path="/downloads")
        self.assertEqual(sha, infohash_from_bytes(TORRENT))
        self.assertFalse(any(p == "/api/v2/torrents/add" for p, _ in self.captured["requests"]))

    def test_list_state_mapping(self):
        base = {
            "hash": "abc", "name": "ep01", "size": 100, "completed": 40,
            "downloaded": 40, "uploaded": 15, "dlspeed": 2048, "eta": 30, "seq_dl": True,
            "added_on": 1000, "save_path": "/downloads", "progress": 0.4,
        }
        for state, expected in [
            ("pausedDL", TaskState.PAUSED),
            ("stoppedDL", TaskState.PAUSED),
            ("downloading", TaskState.DOWNLOADING),
            ("metaDL", TaskState.DOWNLOADING),
            ("uploading", TaskState.SEEDING),
            ("stalledUP", TaskState.SEEDING),
            ("pausedUP", TaskState.COMPLETED),
            ("error", TaskState.FAILED),
        ]:
            self.captured["torrents"] = [dict(base, state=state)]
            task = self.engine.list()[0]
            self.assertEqual(task.state, expected, f"state={state}")
        self.captured["torrents"] = [dict(base, state="pausedDL")]
        task = self.engine.list()[0]
        self.assertEqual(task.eta, 30)
        self.assertEqual(task.rate_down, 2048)
        self.assertEqual(task.uploaded, 15)
        self.assertTrue(task.sequential)
        self.assertEqual(task.added_at, 1000)

    def test_eta_unknown_maps_to_none(self):
        self.captured["torrents"] = [
            {"hash": "abc", "name": "ep", "state": "downloading", "eta": 8640000,
             "size": 10, "completed": 0, "downloaded": 0, "dlspeed": 0,
             "seq_dl": False, "added_on": 0, "save_path": "", "progress": 0}
        ]
        self.assertIsNone(self.engine.list()[0].eta)

    def test_resume_posts_hash(self):
        self.captured["torrents"] = [
            {"hash": "abc", "name": "ep", "state": "pausedDL", "eta": 8640000,
             "size": 10, "completed": 0, "downloaded": 0, "dlspeed": 0,
             "seq_dl": False, "added_on": 0, "save_path": "", "progress": 0}
        ]
        self.engine.resume("abc")
        paths = [p for p, _ in self.captured["requests"]]
        self.assertTrue(any("resume" in p or "start" in p for p in paths))

    def test_remove_unknown_task_raises(self):
        with self.assertRaises(EngineError):
            self.engine.remove("deadbeef")

    def test_add_magnet_posts_urls(self):
        from mqd.torrents import infohash_from_bytes as ifh

        sha = ifh(TORRENT)
        uri = f"magnet:?xt=urn:btih:{sha}&tr=http%3a%2f%2ft.example%2fannounce"
        got = self.engine.add_magnet(uri, paused=False, save_path="/dl")
        self.assertEqual(got, sha)
        posts = [raw for path, raw in self.captured["requests"] if path == "/api/v2/torrents/add"]
        self.assertEqual(len(posts), 1)
        self.assertIn(b"magnet", posts[0])
        self.assertIn(b"urls=magnet", posts[0])

    def test_add_magnet_idempotent(self):
        self.captured["torrents"].append({"hash": infohash_from_bytes(TORRENT), "state": "downloading"})
        uri = f"magnet:?xt=urn:btih:{infohash_from_bytes(TORRENT)}"
        sha = self.engine.add_magnet(uri, paused=False, save_path="/dl")
        self.assertEqual(sha, infohash_from_bytes(TORRENT))
        self.assertFalse(any(p.endswith("/add") for p, _ in self.captured["requests"]))

    def test_rate_limits(self):
        self.engine.set_download_limit(1024)
        self.engine.set_upload_limit(None)  # None → 0 = 不限速
        posts = dict(self.captured["requests"])
        down = [raw for path, raw in self.captured["requests"] if "setDownloadLimit" in path]
        up = [raw for path, raw in self.captured["requests"] if "setUploadLimit" in path]
        self.assertEqual(len(down), 1)
        self.assertEqual(len(up), 1)
        self.assertIn(b"1024", down[0])
        self.assertIn(b"0", up[0])

    def test_bind_ip_applied_on_start(self):
        engine = QbtWebuiEngine(self.url, "admin", "pw", bind_ip="192.168.1.10")
        engine.start()
        prefs = [raw for path, raw in self.captured["requests"] if "setPreferences" in path]
        self.assertEqual(len(prefs), 1)
        self.assertIn(b"192.168.1.10", prefs[0])


if __name__ == "__main__":
    unittest.main()
