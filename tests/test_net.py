import unittest

from server.engine.net import list_bind_candidates


class NetTest(unittest.TestCase):
    def test_returns_bind_candidates(self):
        candidates = list_bind_candidates()
        self.assertIsInstance(candidates, list)
        for name, ip in candidates:
            self.assertIsInstance(name, str)
            self.assertIsInstance(ip, str)
            self.assertFalse(ip.startswith("127."))  # 回环地址不作为绑定候选

    def test_no_duplicate_ips(self):
        ips = [ip for _, ip in list_bind_candidates()]
        self.assertEqual(len(ips), len(set(ips)))


if __name__ == "__main__":
    unittest.main()
