"""测试共享夹具：FakeMikan（Mikan 客户端内存实现）。"""

from mqd.mikan import Episode
from mqd.session import CloudflareBlocked

from test_quota_guard import make_torrent


class FakeMikan:
    """可配置行为的假 Mikan：mode=ok/blocked/error，feeds 保存预设订阅源。"""

    def __init__(self, mode="ok"):
        self.mode = mode
        self.feeds = {}  # url -> (title, [Episode])
        self.sizes = {}  # guid -> 种子字节数（决定限额行为）

    def set_feed(self, url, title, episodes):
        self.feeds[url] = (title, episodes)

    def fetch_feed(self, rss_url=""):
        self._check()
        return self.feeds.get(rss_url, ("", []))

    def download_torrent(self, episode):
        self._check()
        size = self.sizes.get(episode.guid, 10)
        tag = b"%02x" % (abs(hash(episode.guid)) % 256)
        return make_torrent(size, tag)

    def _check(self):
        if self.mode == "blocked":
            raise CloudflareBlocked("Cloudflare 拦截了请求（HTTP 403）")
        if self.mode == "error":
            raise RuntimeError("网络错误")


def make_episode(guid, title="", published=0.0):
    return Episode(
        guid=guid,
        title=title or guid,
        page_url=f"https://mikan.example/Home/Episode/{guid}",
        published=published,
    )
