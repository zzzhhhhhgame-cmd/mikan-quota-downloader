"""LibtorrentEngine 测试：本机装了 libtorrent 就做真实冒烟测试；没装则验证报错友好。"""

import tempfile
import unittest

from server.engine.base import Engine, EngineError
from server.engine.libtorrent_engine import LIBTORRENT_AVAILABLE, LibtorrentEngine

# 合法的最小种子：libtorrent 要求 info 里必须有 piece length 与 pieces（20 字节哈希）
_PIECES = bytes(range(20))
TORRENT = (
    b"d4:infod6:lengthi4096e4:name2:sm12:piece lengthi16384e6:pieces20:"
    + _PIECES
    + b"ee"
)


@unittest.skipUnless(LIBTORRENT_AVAILABLE, "本机未安装 libtorrent（brew/pip 安装后自动启用）")
class LibtorrentSmokeTest(unittest.TestCase):
    def test_add_pause_resume_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = LibtorrentEngine(listen_port=0, save_path_default=tmp)
            try:
                sha = engine.add(TORRENT, paused=True, save_path=tmp)
                tasks = engine.list()
                self.assertEqual(len(tasks), 1)
                self.assertEqual(tasks[0].sha, sha)
                self.assertEqual(tasks[0].state.value, "paused")
                self.assertEqual(tasks[0].size, 4096)

                engine.resume(sha)
                engine.pause(sha)
                engine.remove(sha)
                self.assertEqual(engine.list(), [])
            finally:
                engine.stop()

    def test_duplicate_add_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = LibtorrentEngine(listen_port=0, save_path_default=tmp)
            try:
                sha1 = engine.add(TORRENT, paused=True, save_path=tmp)
                sha2 = engine.add(TORRENT, paused=True, save_path=tmp)
                self.assertEqual(sha1, sha2)
                self.assertEqual(len(engine.list()), 1)
            finally:
                engine.stop()

    def test_invalid_torrent_raises_engine_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = LibtorrentEngine(listen_port=0, save_path_default=tmp)
            try:
                with self.assertRaises(EngineError):
                    engine.add(b"not-a-torrent", paused=True, save_path=tmp)
            finally:
                engine.stop()

    def test_conforms_to_engine_interface(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = LibtorrentEngine(listen_port=0, save_path_default=tmp)
            try:
                self.assertIsInstance(engine, Engine)
            finally:
                engine.stop()


@unittest.skipIf(LIBTORRENT_AVAILABLE, "本机已安装 libtorrent")
class LibtorrentMissingTest(unittest.TestCase):
    def test_init_error_is_actionable(self):
        with self.assertRaises(EngineError) as ctx:
            LibtorrentEngine()
        self.assertIn("libtorrent", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
