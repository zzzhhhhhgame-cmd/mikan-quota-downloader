"""Mikan 站点交互：解析「我的订阅」聚合 RSS，抓取新集数的 .torrent 文件。"""

import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin

from feedparser import parse as parse_feed

# 条目页中的种子下载按钮，例如 href="/Download/202401/xxxx.torrent"
DOWNLOAD_HREF = re.compile(r'href="(/Download/[^"]+)"')


@dataclass
class Episode:
    guid: str
    title: str
    page_url: str
    published: float


class MikanClient:
    def __init__(self, http, rss_url):
        self.http = http
        self.rss_url = rss_url

    def fetch_episodes(self):
        """返回订阅更新条目，按发布时间从旧到新（限额排队时优先补旧集）。"""
        xml = self.http.get_text(self.rss_url)
        feed = parse_feed(xml)
        episodes = []
        for entry in feed.entries:
            link = entry.get("link") or entry.get("id") or ""
            published = time.mktime(entry.published_parsed) if entry.get("published_parsed") else 0.0
            episodes.append(
                Episode(
                    guid=entry.get("id") or link,
                    title=entry.get("title", ""),
                    page_url=link,
                    published=published,
                )
            )
        episodes.sort(key=lambda ep: ep.published)
        return episodes

    def download_torrent(self, episode: Episode) -> bytes:
        if "/Download/" in episode.page_url:
            return self.http.get(episode.page_url)
        html = self.http.get_text(episode.page_url)
        match = DOWNLOAD_HREF.search(html)
        if not match:
            raise RuntimeError(f"在页面中未找到种子下载链接: {episode.page_url}")
        return self.http.get(urljoin(self.http.base, match.group(1)))
