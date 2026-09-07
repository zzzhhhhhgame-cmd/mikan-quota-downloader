"""qBittorrent WebUI API v2 客户端，兼容 4.x 与 5.x 的参数/端点命名差异
（paused→stopped、resume→start）。"""

import requests


class QbtError(RuntimeError):
    pass


class QbtClient:
    def __init__(self, base_url, username, password):
        self.base = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()

    def login(self):
        resp = self.session.post(
            f"{self.base}/api/v2/auth/login",
            data={"username": self.username, "password": self.password},
            timeout=10,
        )
        resp.raise_for_status()
        if resp.text.strip() != "Ok.":
            raise QbtError(f"qBittorrent 登录失败: {resp.text.strip()}")

    def torrents(self, category=None):
        params = {"category": category} if category else {}
        resp = self.session.get(f"{self.base}/api/v2/torrents/info", params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    # ---- 种子状态判定（客户端侧过滤，绕开 4.x/5.x 的 filter 取值差异）----

    DONE_STATES = {
        "uploading", "pausedUP", "stoppedUP", "stalledUP",
        "queuedUP", "checkingUP", "forcedUP", "completed",
    }

    @classmethod
    def is_done(cls, torrent):
        return float(torrent.get("progress", 0)) >= 1 or torrent.get("state") in cls.DONE_STATES

    @classmethod
    def is_paused(cls, torrent):
        return torrent.get("state") in ("pausedDL", "stoppedDL")

    @staticmethod
    def need_bytes(torrent):
        """该种子还需下载的字节数（限额判定的「在途剩余」）。"""
        return max(0, int(torrent.get("size", 0)) - int(torrent.get("completed", 0)))

    # ---- 操作 ----

    def add_torrent(self, data, *, paused, category, save_path=None, ratio_limit=None):
        fields = {
            "autoTMM": "false",
            "category": category,
            "paused": "true" if paused else "false",   # qBt 4.x
            "stopped": "true" if paused else "false",  # qBt 5.x
        }
        if save_path:
            fields["savepath"] = save_path
        if ratio_limit is not None:
            fields["ratioLimit"] = str(ratio_limit)
        files = {"torrents": ("episode.torrent", data, "application/x-bittorrent")}
        resp = self.session.post(
            f"{self.base}/api/v2/torrents/add", data=fields, files=files, timeout=30
        )
        resp.raise_for_status()
        return "Ok" in resp.text

    def resume(self, hashes):
        self._post_compat("torrents/resume", "torrents/start", hashes=hashes)

    def _post_compat(self, *paths, hashes):
        body = {"hashes": "|".join(hashes)}
        for path in paths:
            resp = self.session.post(f"{self.base}/api/v2/{path}", data=body, timeout=15)
            if resp.status_code == 200:
                return
        raise QbtError(f"调用 {paths} 失败")
