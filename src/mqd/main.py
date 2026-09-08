"""单轮检查逻辑：归集流量 → 放行等待队列 → 拉订阅 → 限额判定入队。"""

import logging
import time

from .mikan import MikanClient
from .qbittorrent import QbtClient
from .quota import DailyQuota, GiB
from .session import HttpClient
from .store import Store
from .torrents import torrent_size

log = logging.getLogger("mqd")


def _today():
    return time.strftime("%Y-%m-%d")


def run_once(cfg):
    mikan_cfg = cfg["mikan"]
    qbt_cfg = cfg["qbittorrent"]
    mon_cfg = cfg["monitor"]
    category = qbt_cfg.get("category", "bangumi")

    store = Store(mon_cfg.get("db_file", "data/state.db"))
    quota = DailyQuota(int(float(cfg["quota"].get("daily_limit_gb", 30)) * GiB))
    qbt = QbtClient(qbt_cfg["base_url"], qbt_cfg.get("username", ""), qbt_cfg.get("password", ""))
    qbt.login()

    http = HttpClient(
        mikan_cfg["base_url"],
        session_file=mikan_cfg.get("session_file"),
        cookie_string=mikan_cfg.get("cookie_string"),
        user_agent=mikan_cfg.get("user_agent"),
    )
    mikan = MikanClient(http, mikan_cfg["rss_url"])

    # 1) 归集今日下载量，并计算在途种子的剩余需求
    torrents = qbt.torrents(category=category)
    usage = store.attribute_all(torrents, _today())
    used_today = usage.down
    active = [t for t in torrents if not QbtClient.is_done(t)]
    active_remaining = sum(QbtClient.need_bytes(t) for t in active)

    # 2) 预算允许时按加入先后放行等待队列（小体积集数可插空，充分利用预算）
    waiting = sorted(
        (t for t in active if QbtClient.is_paused(t)), key=lambda t: t.get("added_on", 0)
    )
    for t in waiting:
        need = QbtClient.need_bytes(t)
        if need <= quota.remaining(used_today, active_remaining):
            log.info("预算允许，恢复下载: %s", t.get("name"))
            qbt.resume([t["hash"]])
            active_remaining += need
        else:
            log.info("预算不足，继续等待: %s（还需 %.2f GiB）", t.get("name"), need / GiB)

    # 3) 拉取订阅更新
    episodes = mikan.fetch_episodes()
    fresh = [ep for ep in episodes if not store.seen(ep.guid)]
    if not fresh:
        log.info(
            "订阅无更新（今日下载 %.2f / %.2f GiB，上传 %.2f GiB）",
            used_today / GiB,
            quota.limit / GiB,
            usage.up / GiB,
        )
        return

    for ep in fresh:
        try:
            raw = mikan.download_torrent(ep)
            size = torrent_size(raw)
            decision = quota.decide(size, used_today, active_remaining)
            ok = qbt.add_torrent(
                raw,
                paused=not decision.start,
                category=category,
                save_path=qbt_cfg.get("save_path") or None,
                ratio_limit=qbt_cfg.get("ratio_limit"),
            )
            if not ok:
                log.error("qBittorrent 拒绝了种子: %s", ep.title)
                continue
            store.mark_seen(ep.guid, title=ep.title, added_at=time.time())
            if decision.start:
                active_remaining += size
            log.info("%s: %s（%s）", "开始下载" if decision.start else "排队", ep.title, decision.reason)
        except CloudflareBlocked:
            log.error("会话被 Cloudflare 拦截，本轮终止；请运行 python -m mqd.login")
            return
        except Exception:
            log.exception("处理条目失败，跳过: %s", ep.title)
