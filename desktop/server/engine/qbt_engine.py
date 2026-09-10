"""后备引擎：驱动外部 qBittorrent（WebUI API v2），实现与内嵌引擎相同的接口。

用于 libtorrent 打包受阻的平台，或用户显式选择"qBittorrent 做引擎、本应用做 UI"。
对端 qBittorrent 只需开启 WebUI，无需显示界面（macOS 有 qbittorrent-nox）。
"""

from __future__ import annotations

import json

import requests

from mqd.torrents import infohash_from_bytes, magnet_infohash

from .base import Engine, EngineError, TaskState, TorrentState

_SEEDING_STATES = {"uploading", "stalledUP", "forcedUP", "completed"}
_DONE_STATES = {"pausedUP", "stoppedUP", "queuedUP"}
_PAUSED_STATES = {"pausedDL", "stoppedDL"}
_FAILED_STATES = {"error", "missingFiles"}
_ETA_UNKNOWN = 8640000  # qBt 的 eta 未知哨兵值


class QbtWebuiEngine(Engine):
    def __init__(self, base_url: str, username: str = "admin", password: str = "", category: str = "bangumi", bind_ip: str | None = None):
        self.base = base_url.rstrip("/")
        self.category = category
        self._username = username
        self._password = password
        # bind_ip：BT 直连（绕过 VPN），映射到 qBt 的网络接口绑定；None=跟随系统路由
        self._bind_ip = bind_ip
        self.session = requests.Session()

    # ---- 生命周期 ----

    def start(self):
        resp = self.session.post(
            f"{self.base}/api/v2/auth/login",
            data={"username": self._username, "password": self._password},
            timeout=10,
        )
        resp.raise_for_status()
        if resp.text.strip() != "Ok.":
            raise EngineError(f"qBittorrent 登录失败: {resp.text.strip()}")
        if self._bind_ip:
            resp = self.session.post(
                f"{self.base}/api/v2/app/setPreferences",
                data={"json": json.dumps({"current_interface_address": self._bind_ip})},
                timeout=10,
            )
            resp.raise_for_status()

    def stop(self):
        pass  # qBt 是外部进程，生命周期由它自己管理

    # ---- Engine 接口 ----

    def add(self, data: bytes, *, paused: bool, save_path: str, sequential: bool = False, category: str = "") -> str:
        sha = infohash_from_bytes(data)
        if any(t.sha == sha for t in self.list()):
            return sha  # 幂等
        fields = {
            "autoTMM": "false",
            "category": category or self.category,
            "paused": "true" if paused else "false",  # qBt 4.x
            "stopped": "true" if paused else "false",  # qBt 5.x
        }
        if save_path:
            fields["savepath"] = save_path
        if sequential:
            fields["sequentialDownload"] = "true"
        files = {"torrents": ("episode.torrent", data, "application/x-bittorrent")}
        resp = self.session.post(
            f"{self.base}/api/v2/torrents/add", data=fields, files=files, timeout=30
        )
        resp.raise_for_status()
        if "Ok" not in resp.text:
            raise EngineError(f"qBittorrent 拒绝了种子: {resp.text.strip()}")
        return sha

    def add_magnet(self, uri: str, *, paused: bool, save_path: str, sequential: bool = False, category: str = "") -> str:
        sha = magnet_infohash(uri)
        if any(t.sha == sha for t in self.list()):
            return sha  # 幂等
        fields = {
            "autoTMM": "false",
            "category": category or self.category,
            "urls": uri.strip(),
            "paused": "true" if paused else "false",
            "stopped": "true" if paused else "false",
        }
        if save_path:
            fields["savepath"] = save_path
        if sequential:
            fields["sequentialDownload"] = "true"
        resp = self.session.post(f"{self.base}/api/v2/torrents/add", data=fields, timeout=30)
        resp.raise_for_status()
        if "Ok" not in resp.text:
            raise EngineError(f"qBittorrent 拒绝了磁力链接: {resp.text.strip()}")
        return sha

    def move_storage(self, sha: str, new_path: str):
        hashes = self._require_known(sha)
        resp = self.session.post(
            f"{self.base}/api/v2/torrents/setLocation",
            data={"hashes": hashes, "location": new_path},
            timeout=30,
        )
        resp.raise_for_status()

    def pause(self, sha: str):
        self._post_compat("torrents/pause", "torrents/stop", sha=sha)

    def resume(self, sha: str):
        self._post_compat("torrents/resume", "torrents/start", sha=sha)

    def remove(self, sha: str, with_files: bool = False):
        hashes = self._require_known(sha)
        resp = self.session.post(
            f"{self.base}/api/v2/torrents/delete",
            data={"hashes": hashes, "deleteFiles": "true" if with_files else "false"},
            timeout=15,
        )
        resp.raise_for_status()

    def list(self) -> list[TorrentState]:
        resp = self.session.get(
            f"{self.base}/api/v2/torrents/info", params={"category": self.category}, timeout=15
        )
        resp.raise_for_status()
        tasks = []
        for t in resp.json():
            raw_state = str(t.get("state", ""))
            if raw_state in _FAILED_STATES:
                state = TaskState.FAILED
            elif raw_state in _PAUSED_STATES:
                state = TaskState.PAUSED
            elif raw_state in _SEEDING_STATES or float(t.get("progress", 0)) >= 1:
                state = TaskState.SEEDING  # 做种中
            elif raw_state in _DONE_STATES:
                state = TaskState.COMPLETED  # 下载完成但未在 做种/上传
            else:
                state = TaskState.DOWNLOADING
            eta = int(t.get("eta", _ETA_UNKNOWN))
            tasks.append(
                TorrentState(
                    sha=str(t.get("hash", "")),
                    name=str(t.get("name", "")),
                    state=state,
                    size=int(t.get("size", 0)),
                    done=int(t.get("completed", 0)),
                    downloaded=int(t.get("downloaded", 0)),
                    uploaded=int(t.get("uploaded", 0)),
                    rate_down=int(t.get("dlspeed", 0)),
                    rate_up=int(t.get("upspeed", 0)),
                    eta=eta if 0 <= eta < _ETA_UNKNOWN else None,
                    sequential=bool(t.get("seq_dl", False)),
                    save_path=str(t.get("save_path", "")),
                    added_at=float(t.get("added_on", 0) or 0),
                    error=raw_state if state == TaskState.FAILED else None,
                )
            )
        tasks.sort(key=lambda t: t.added_at)
        return tasks

    def set_download_limit(self, down_bps: int | None):
        self._post_rate("/api/v2/transfer/setDownloadLimit", down_bps)

    def set_upload_limit(self, up_bps: int | None):
        self._post_rate("/api/v2/transfer/setUploadLimit", up_bps)

    def _post_rate(self, path: str, bps: int | None):
        resp = self.session.post(
            f"{self.base}{path}", data={"limit": str(int(bps or 0))}, timeout=15  # 0=不限速
        )
        resp.raise_for_status()

    # ---- 内部 ----

    def _require_known(self, sha: str) -> str:
        """qBt 的暂停/删除按 hash 操作；校验目标存在以免误传空串（all）。"""
        if any(t.sha == sha for t in self.list()):
            return sha
        raise EngineError(f"任务不存在: {sha}")

    def _post_compat(self, *paths, sha: str):
        hashes = self._require_known(sha)
        for path in paths:
            resp = self.session.post(
                f"{self.base}/api/v2/{path}", data={"hashes": hashes}, timeout=15
            )
            if resp.status_code == 200:
                return
        raise EngineError(f"调用 {paths} 失败")
