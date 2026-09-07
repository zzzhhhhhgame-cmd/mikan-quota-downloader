import tempfile
import unittest

from mqd.quota import DailyQuota, GiB
from mqd.store import Store
from mqd.torrents import torrent_size

DAY = "2026-09-07"


class QuotaDecisionTest(unittest.TestCase):
    def test_within_budget_starts(self):
        quota = DailyQuota(30 * GiB)
        self.assertTrue(quota.decide(10 * GiB, 10 * GiB, 5 * GiB).start)  # 剩 15GiB

    def test_exactly_fits_starts(self):
        quota = DailyQuota(30 * GiB)
        self.assertTrue(quota.decide(15 * GiB, 10 * GiB, 5 * GiB).start)

    def test_over_budget_queues(self):
        quota = DailyQuota(30 * GiB)
        decision = quota.decide(16 * GiB, 10 * GiB, 5 * GiB)
        self.assertFalse(decision.start)

    def test_negative_budget_clamped(self):
        quota = DailyQuota(30 * GiB)
        self.assertEqual(quota.remaining(35 * GiB, 0), 0)


class LedgerTest(unittest.TestCase):
    def test_delta_attribution(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(f"{tmp}/state.db")
            used = store.attribute_all([{"hash": "a", "size": 100, "downloaded": 30}], DAY)
            self.assertEqual(used, 30)
            used = store.attribute_all([{"hash": "a", "size": 100, "downloaded": 80}], DAY)
            self.assertEqual(used, 80)
            used = store.attribute_all([{"hash": "a", "size": 100, "downloaded": 80}], DAY)
            self.assertEqual(used, 80)  # 无增量不重复计

    def test_days_are_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(f"{tmp}/state.db")
            store.attribute_all([{"hash": "a", "size": 100, "downloaded": 50}], DAY)
            used_next = store.attribute_all([{"hash": "a", "size": 100, "downloaded": 90}], "2026-09-08")
            self.assertEqual(used_next, 40)  # 新的一天只记新发生的字节


class TorrentSizeTest(unittest.TestCase):
    def test_single_file(self):
        raw = b"d4:infod6:lengthi1073741824e4:name4:testee"
        self.assertEqual(torrent_size(raw), 1073741824)

    def test_multi_file(self):
        raw = b"d4:infod5:filesld6:lengthi5eed6:lengthi7eeeee"
        self.assertEqual(torrent_size(raw), 12)


if __name__ == "__main__":
    unittest.main()
