"""应用上下文：装配 引擎/记账/限额/会话/调度器，供 API 与桌面壳共享。"""

from __future__ import annotations

from dataclasses import dataclass, field

from mqd.quota import DailyQuota, GiB
from mqd.store import Store

from ..engine.base import Engine, EngineError
from ..engine.libtorrent_engine import LIBTORRENT_AVAILABLE, LibtorrentEngine
from ..engine.qbt_engine import QbtWebuiEngine
from .quota_guard import QuotaGuard
from .scheduler import SyncScheduler
from .session_manager import SessionManager


@dataclass
class AppContext:
    cfg: dict
    engine: Engine
    store: Store
    guard: QuotaGuard
    sessions: SessionManager
    scheduler: SyncScheduler | None = None
    # app.py 注入：桌面 webview 会话收割（HTTP 端点代理调用；浏览器环境为 None）
    harvest_callback: object | None = field(default=None)

    def start(self):
        self.engine.start()
        if self.scheduler is not None:
            self.scheduler.start()

    def close(self):
        if self.scheduler is not None:
            self.scheduler.stop()
        try:
            self.engine.stop()
        except Exception:
            pass


def build_context(cfg: dict) -> AppContext:
    """按配置装配真实依赖（测试直接手工构造 AppContext）。"""
    desktop_cfg = cfg.get("desktop", {})
    engine = _build_engine(cfg)

    store = Store(cfg["monitor"].get("db_file", "data/state.db"))
    download_limit = float(cfg["quota"].get("daily_limit_gb", 30))
    seed_limit = float(cfg["quota"].get("seed_limit_gb") or 0)
    guard = QuotaGuard(
        engine,
        store,
        download_quota=DailyQuota(int(download_limit * GiB)),
        seed_limit_bytes=int(seed_limit * GiB),
    )
    sessions = SessionManager(
        cfg["mikan"]["base_url"],
        cfg["mikan"].get("session_file", "data/session.json"),
    )
    scheduler = SyncScheduler(guard, int(cfg["monitor"].get("interval_minutes", 20)))
    return AppContext(
        cfg=cfg, engine=engine, store=store, guard=guard, sessions=sessions, scheduler=scheduler
    )


def _build_engine(cfg: dict) -> Engine:
    desktop_cfg = cfg.get("desktop", {})
    choice = desktop_cfg.get("engine", "auto")
    bind_ip = desktop_cfg.get("bind_ip") or None

    if choice in ("auto", "libtorrent"):
        if LIBTORRENT_AVAILABLE:
            return LibtorrentEngine(
                listen_port=int(desktop_cfg.get("bt_port", 6881)),
                save_path_default=desktop_cfg.get("save_path", "."),
                bind_ip=bind_ip,
            )
        if choice == "libtorrent":
            raise EngineError("desktop.engine=libtorrent 但本机未安装 libtorrent")

    qbt = cfg["qbittorrent"]
    return QbtWebuiEngine(
        qbt["base_url"],
        qbt.get("username", "admin"),
        qbt.get("password", ""),
        category=qbt.get("category", "bangumi"),
        bind_ip=bind_ip,
    )
