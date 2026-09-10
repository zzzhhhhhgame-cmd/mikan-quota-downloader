"""MikanClient 单测：频道标题提取（feedparser 的标题在 channel 节点）与条目解析。"""

import unittest

from mqd.mikan import MikanClient

SAMPLE_XML = """<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>
<title>Mikan Project - 擅长逃跑的殿下 第二季</title>
<link>https://mikan.example/RSS/Bangumi?bangumiId=4039&amp;subgroupid=370</link>
<item><guid isPermaLink="false">ep-07</guid>
<link>https://mikan.example/Home/Episode/abc</link>
<title>[LoliHouse] 擅长逃跑的殿下 第二季 - 07 [WebRip]</title>
<pubDate>Mon, 08 Sep 2026 14:00:00 GMT</pubDate></item>
</channel></rss>"""


class FakeHttp:
    def __init__(self, xml):
        self.xml = xml

    def get_text(self, url):
        return self.xml


class MikanClientTest(unittest.TestCase):
    def test_feed_title_from_channel_node(self):
        client = MikanClient(FakeHttp(SAMPLE_XML), "https://mikan.example/RSS/Bangumi?bangumiId=1")
        title, episodes = client.fetch_feed()
        self.assertEqual(title, "Mikan Project - 擅长逃跑的殿下 第二季")
        self.assertEqual(len(episodes), 1)
        self.assertIn("LoliHouse", episodes[0].title)

    def test_empty_title_falls_back_to_empty_string(self):
        xml = SAMPLE_XML.replace(
            "<title>Mikan Project - 擅长逃跑的殿下 第二季</title>", ""
        )
        client = MikanClient(FakeHttp(xml), "https://mikan.example/RSS/Bangumi?bangumiId=1")
        title, episodes = client.fetch_feed()
        self.assertEqual(title, "")
        self.assertEqual(len(episodes), 1)


if __name__ == "__main__":
    unittest.main()
