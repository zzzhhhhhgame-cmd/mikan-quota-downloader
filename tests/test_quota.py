import sqlite3
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
            usage = store.attribute_all([{"hash": "a", "size": 100, "downloaded": 30}], DAY)
            self.assertEqual(usage.down, 30)
            usage = store.attribute_all([{"hash": "a", "size": 100, "downloaded": 80}], DAY)
            self.assertEqual(usage.down, 80)
            usage = store.attribute_all([{"hash": "a", "size": 100, "downloaded": 80}], DAY)
            self.assertEqual(usage.down, 80)  # 无增量不重复计

    def test_upload_attribution(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(f"{tmp}/state.db")
            usage = store.attribute_all(
                [{"hash": "a", "size": 100, "downloaded": 10, "uploaded": 5}], DAY
            )
            self.assertEqual((usage.down, usage.up), (10, 5))
            usage = store.attribute_all(
                [{"hash": "a", "size": 100, "downloaded": 80, "uploaded": 12}], DAY
            )
            self.assertEqual((usage.down, usage.up), (80, 12))

    def test_days_are_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(f"{tmp}/state.db")
            store.attribute_all([{"hash": "a", "size": 100, "downloaded": 50}], DAY)
            usage = store.attribute_all([{"hash": "a", "size": 100, "downloaded": 90}], "2026-09-08")
            self.assertEqual(usage.down, 40)  # 新的一天只记新发生的字节

    def test_migrates_legacy_db(self):
        """V1 早期库没有 uploaded/up_used 列，打开时应自动补列。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/legacy.db"
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                CREATE TABLE seen(guid TEXT PRIMARY KEY, torrent_hash TEXT, title TEXT, added_at REAL);
                CREATE TABLE ledger(hash TEXT PRIMARY KEY, size INTEGER, downloaded INTEGER);
                CREATE TABLE daily(day TEXT PRIMARY KEY, used INTEGER);
                INSERT INTO ledger VALUES ('old', 100, 50);
                """
            )
            conn.commit()
            conn.close()
            store = Store(path)
            usage = store.attribute_all(
                [{"hash": "old", "size": 100, "downloaded": 60, "uploaded": 7}], DAY
            )
            self.assertEqual((usage.down, usage.up), (10, 7))  # 旧 downloaded=50 被继承，只记增量


class TorrentSizeTest(unittest.TestCase):
    def test_single_file(self):
        raw = b"d4:infod6:lengthi1073741824e4:name4:testee"
        self.assertEqual(torrent_size(raw), 1073741824)

    def test_multi_file(self):
        raw = b"d4:infod5:filesld6:lengthi5eed6:lengthi7eeeee"
        self.assertEqual(torrent_size(raw), 12)


if __name__ == "__main__":
    unittest.main()
