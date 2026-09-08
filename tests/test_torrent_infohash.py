import unittest

from mqd.torrents import _decode, info_span, infohash_from_bytes, torrent_size

SINGLE = b"d4:infod6:lengthi1073741824e4:name4:testee"
MULTI = b"d4:infod5:filesld6:lengthi5eed6:lengthi7eeeee"


class InfoSpanTest(unittest.TestCase):
    def test_span_roundtrip_single(self):
        start, end = info_span(SINGLE)
        value, _ = _decode(SINGLE[start:end], 0)
        full, _ = _decode(SINGLE, 0)
        self.assertEqual(value, full[b"info"])

    def test_span_roundtrip_multi(self):
        start, end = info_span(MULTI)
        value, _ = _decode(MULTI[start:end], 0)
        full, _ = _decode(MULTI, 0)
        self.assertEqual(value, full[b"info"])

    def test_infohash_deterministic(self):
        self.assertEqual(infohash_from_bytes(SINGLE), infohash_from_bytes(SINGLE))
        self.assertNotEqual(infohash_from_bytes(SINGLE), infohash_from_bytes(MULTI))

    def test_infohash_is_sha1_hex(self):
        digest = infohash_from_bytes(SINGLE)
        self.assertEqual(len(digest), 40)
        int(digest, 16)  # 合法 hex

    def test_missing_info_raises(self):
        with self.assertRaises(ValueError):
            info_span(b"d4:name4:teste")


class SizeTest(unittest.TestCase):
    def test_single(self):
        self.assertEqual(torrent_size(SINGLE), 1073741824)

    def test_multi(self):
        self.assertEqual(torrent_size(MULTI), 12)


if __name__ == "__main__":
    unittest.main()
