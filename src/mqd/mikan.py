"""Mikan 站点交互：解析任意 Mikan RSS 订阅源（番剧 RSS / 个人聚合 RSS 皆可），
抓取新集数的 .torrent 文件。无需站点账号——被 Cloudflare 拦截时给出导入 Cookie 的指引。"""

import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin

from feedparser import parse as parse_feed

from .session import HttpClient

# 条目页中的种子下载按钮，例如 href="/Download/202401/xxxx.torrent"
DOWNLOAD_HREF = re.compile(r'href="(/Download/[^"]+)"')


@dataclass
class Episode:
    guid: str
    title: str
    page_url: str
    published: float
    torrent_url: str = ""  # 部分订阅源直接给出种子地址，可省去页面解析


class MikanClient:
    def __init__(self, http: HttpClient, rss_url: str = ""):
        self.http = http
        self.rss_url = rss_url  # 默认订阅源（V1 聚合 RSS）；RSS 订阅管理可逐次指定

    def fetch_feed(self, rss_url: str = ""):
        """返回 (订阅源标题, 条目列表)，条目按发布时间从旧到新（限额排队时优先补旧集）。"""
        xml = self.http.get_text(rss_url or self.rss_url)
        feed = parse_feed(xml)
        episodes = []
        for entry in feed.entries:
            link = entry.get("link") or entry.get("id") or ""
            published = time.mktime(entry.published_parsed) if entry.get("published_parsed") else 0.0
            torrent_url = ""
            for enclosure in entry.get("enclosures", []):
                if enclosure.get("href"):
                    torrent_url = enclosure["href"]
                    break
            episodes.append(
                Episode(
                    guid=entry.get("id") or link,
                    title=entry.get("title", ""),
                    page_url=link,
                    published=published,
                    torrent_url=torrent_url,
                )
            )
        episodes.sort(key=lambda ep: ep.published)
        return feed.get("title") or "", episodes

    def fetch_episodes(self, rss_url: str = ""):
        return self.fetch_feed(rss_url)[1]

    def download_torrent(self, episode: Episode) -> bytes:
        if episode.torrent_url:
            return self.http.get(episode.torrent_url)
        if "/Download/" in episode.page_url:
            return self.http.get(episode.page_url)
        html = self.http.get_text(episode.page_url)
        match = DOWNLOAD_HREF.search(html)
        if not match:
            raise RuntimeError(f"在页面中未找到种子下载链接: {episode.page_url}")
        return self.http.get(urljoin(self.http.base, match.group(1)))
