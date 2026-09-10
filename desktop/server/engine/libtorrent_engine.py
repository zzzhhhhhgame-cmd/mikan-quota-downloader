"""主线引擎：进程内嵌 libtorrent（qBittorrent 的同款内核）。

线程模型：后台告警线程以 wait_for_alert 节拍循环（默认 500ms），持有锁刷新任务
状态快照；对外方法直接操作 session/handle（libtorrent 会话线程安全，方法均轻量）。

兼容 libtorrent 1.2 与 2.x：infohash 取 v1（与 qBt/本仓库 infohash_from_bytes 一致），
2.x 的纯 v2 种子回退 best()。安装方式见 PLAN-DESKTOP.md 第 8 节。
"""

from __future__ import annotations

import logging
import threading
import time

from mqd.torrents import infohash_from_bytes, magnet_infohash

from .base import Engine, EngineError, TaskState, TorrentState

try:
    import libtorrent as lt

    LIBTORRENT_AVAILABLE = True
except ImportError:  # 让本模块在未安装 libtorrent 的机器上也可导入（测试/文档场景）
    lt = None
    LIBTORRENT_AVAILABLE = False

log = logging.getLogger("mqd.engine.lt")

_ALERT_INTERVAL_S = 0.5


class LibtorrentEngine(Engine):
    def __init__(self, listen_port: int = 6881, save_path_default: str = ".", bind_ip: str | None = None):
        if not LIBTORRENT_AVAILABLE:
            raise EngineError(
                "libtorrent 未安装：Windows/Linux 用 pip install libtorrent；"
                "macOS 需 brew install libtorrent 或源码构建（见 PLAN-DESKTOP.md 第 8 节），"
                "或将引擎切换为 QbtWebuiEngine"
            )
        self._default_save_path = save_path_default
        # bind_ip：BT 直连（绕过 VPN）——监听与出站都绑定物理网卡；None=跟随系统路由。
        # TUN/全隧 VPN 接管默认路由时，绑定物理网卡即可绕开虚拟网卡。
        settings = {
            "listen_interfaces": f"{bind_ip or '0.0.0.0'}:{listen_port}",
            "alert_mask": lt.alert.category_t.status_notification
            | lt.alert.category_t.error_notification,
            "active_downloads": 8,
            "active_seeds": 8,
            "active_limit": 16,
        }
        if bind_ip:
            settings["outgoing_interfaces"] = bind_ip
        self._session = lt.session(settings)
        self._lock = threading.Lock()
        self._states: dict[str, TorrentState] = {}
        self._errors: dict[str, str] = {}
        self._next_seq = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="lt-alerts", daemon=True)
        self._thread.start()

    # ---- 生命周期 ----

    def start(self):
        pass  # __init__ 已启动会话与告警线程

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=3)
        self._session.pause()
        abort = getattr(self._session, "abort", None)  # libtorrent 2.1+ 移除了 abort()
        if callable(abort):
            abort()

    # ---- Engine 接口 ----

    def add(self, data: bytes, *, paused: bool, save_path: str, sequential: bool = False, category: str = "") -> str:
        try:
            ti = lt.torrent_info(lt.bdecode(data))
        except (RuntimeError, ValueError, TypeError, KeyError) as exc:
            # libtorrent 对元数据校验严格（piece 长度/哈希数量等），统一转成契约内异常
            raise EngineError(f"无效的 .torrent 文件：{exc}") from exc
        sha = infohash_from_bytes(data)
        if any(t.sha == sha for t in self.list()):
            return sha  # 幂等：重复添加直接返回已有任务
        atp = lt.add_torrent_params()
        atp.ti = ti
        self._insert(atp, ti, sha, paused=paused, save_path=save_path, sequential=sequential)
        return sha

    def add_magnet(self, uri: str, *, paused: bool, save_path: str, sequential: bool = False, category: str = "") -> str:
        sha = magnet_infohash(uri)
        if any(t.sha == sha for t in self.list()):
            return sha  # 幂等
        try:
            atp = lt.parse_magnet_uri(uri.strip())  # libtorrent 1.2+ / 2.x
        except AttributeError:
            atp = lt.add_torrent_params()
            atp.url = uri.strip()
        # 磁链必须处于运行状态才能获取元数据；体积已知后由 QuotaGuard 补充限额判定
        self._insert(atp, None, sha, paused=paused, save_path=save_path, sequential=sequential)
        return sha

    def _insert(self, atp, ti, sha: str, *, paused: bool, save_path: str, sequential: bool):
        atp.save_path = save_path or self._default_save_path
        flags = atp.flags
        flags |= lt.torrent_flags.paused if paused else lt.torrent_flags.auto_managed
        if sequential:
            flags |= lt.torrent_flags.sequential_download
        atp.flags = flags
        handle = self._session.add_torrent(atp)
        sha = self._sha(handle)
        name = ti.name() if ti is not None else (getattr(atp, "url", "") or sha)[:72]
        size = int(ti.total_size()) if ti is not None else 0  # 磁链元数据到达前体积未知
        with self._lock:
            self._next_seq += 1
            self._states[sha] = TorrentState(
                sha=sha,
                name=name,
                state=TaskState.PAUSED if paused else TaskState.DOWNLOADING,
                size=size,
                sequential=sequential,
                save_path=atp.save_path,
                added_at=time.time(),
                order=self._next_seq,
            )

    def move_storage(self, sha: str, new_path: str):
        handle = self._find(sha)
        if not handle.move_storage(new_path):
            raise EngineError(f"文件搬迁失败（可能磁盘不可写）: {new_path}")

    def pause(self, sha: str):
        handle = self._find(sha)
        handle.unset_flags(lt.torrent_flags.auto_managed)  # 防止队列管理器自动恢复
        handle.pause()

    def resume(self, sha: str):
        handle = self._find(sha)
        handle.set_flags(lt.torrent_flags.auto_managed)
        handle.resume()

    def remove(self, sha: str, with_files: bool = False):
        handle = self._find(sha)
        delete_flag = getattr(getattr(lt, "options_t", None), "delete_files", None)
        if with_files and delete_flag is not None:
            self._session.remove_torrent(handle, delete_flag)
        else:
            if with_files:
                log.warning("当前 libtorrent 绑定不支持 delete_files，仅移除任务")
            self._session.remove_torrent(handle)
        with self._lock:
            self._states.pop(sha, None)
            self._errors.pop(sha, None)

    def list(self) -> list[TorrentState]:
        with self._lock:
            return sorted(self._states.values(), key=lambda t: t.order)

    def set_download_limit(self, down_bps: int | None):
        self._session.apply_settings({"download_rate_limit": int(down_bps or 0)})  # 0=不限速

    def set_upload_limit(self, up_bps: int | None):
        self._session.apply_settings({"upload_rate_limit": int(up_bps or 0)})

    # ---- 内部 ----

    def _find(self, sha: str):
        for handle in self._session.get_torrents():
            if handle.is_valid() and self._sha(handle) == sha:
                return handle
        raise EngineError(f"任务不存在: {sha}")

    @staticmethod
    def _sha(handle) -> str:
        try:
            ihs = handle.info_hashes()  # libtorrent 2.x
            v1 = ihs.v1
            return str(v1) if v1 else str(ihs.best())
        except AttributeError:  # libtorrent 1.2
            return str(handle.info_hash())

    def _loop(self):
        while not self._stop.is_set():
            self._session.wait_for_alert(int(_ALERT_INTERVAL_S * 1000))
            with self._lock:
                for alert in self._session.pop_alerts():
                    self._handle_alert(alert)
                self._refresh()

    def _handle_alert(self, alert):
        name = type(alert).__name__
        if "error" not in name.lower():
            return
        handle = getattr(alert, "handle", None)
        if handle is not None and handle.is_valid():
            self._errors[self._sha(handle)] = alert.message()

    def _refresh(self):
        """在持有锁的前提下用各 handle 的 status 刷新状态快照。"""
        seen = set()
        for handle in self._session.get_torrents():
            if not handle.is_valid():
                continue
            sha = self._sha(handle)
            seen.add(sha)
            st = handle.status()
            flags = st.flags
            paused = bool(flags & lt.torrent_flags.paused)
            sequential = bool(flags & lt.torrent_flags.sequential_download)
            size = int(st.total_wanted)
            done = int(st.total_done)
            downloaded = int(st.all_time_download)
            uploaded = int(st.all_time_upload)
            rate = int(st.download_payload_rate)
            rate_up = int(st.upload_payload_rate)
            error = self._errors.pop(sha, None)

            if error is not None:
                state = TaskState.FAILED
            elif paused:
                state = TaskState.PAUSED
            elif st.state == lt.torrent_status.seeding:
                state = TaskState.SEEDING  # 做种中：正在为他人上传
            elif st.state == lt.torrent_status.finished or (size > 0 and done >= size):
                state = TaskState.COMPLETED
            elif st.state == lt.torrent_status.checking_files:
                state = TaskState.CHECKING
            else:
                state = TaskState.DOWNLOADING

            prev = self._states.get(sha)
            self._states[sha] = TorrentState(
                sha=sha,
                name=st.name or (prev.name if prev else sha),
                state=state,
                size=size or (prev.size if prev else 0),
                done=done,
                downloaded=downloaded,
                uploaded=uploaded,
                rate_down=rate,
                rate_up=rate_up,
                eta=max(0, size - done) // rate if state == TaskState.DOWNLOADING and rate > 0 else None,
                sequential=sequential,
                save_path=st.save_path or (prev.save_path if prev else self._default_save_path),
                added_at=prev.added_at if prev else time.time(),
                order=prev.order if prev else self._bump_seq(),
                error=error,
            )
        for sha in [s for s in self._states if s not in seen]:  # 引擎外部移除的任务
            self._states.pop(sha, None)

    def _bump_seq(self) -> int:
        self._next_seq += 1
        return self._next_seq
