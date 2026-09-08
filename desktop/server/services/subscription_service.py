"""RSS 链接订阅服务：粘贴任意 Mikan RSS 链接即可自动追更，无需站点账号。

关于"RSS 链接内容会不会更新"：番剧 RSS 是滚动窗口（只保留最近若干条），
但本服务每次轮询都拉全量条目并用 GUID 在 seen 表去重——新集出现在源里就会
被下载，源滚动不影响订阅语义。掉线超过窗口期漏掉的集数需手动补种。

被 Cloudflare 拦截时：订阅仍会保存，界面提示导入 Cookie（无需登录账号，
只要浏览器能打开站点即可复制）。
"""

from __future__ import annotations

import time

from mqd.session import CloudflareBlocked

from ..engine.base import EngineError

COOKIE_HINT = "（无需登录账号：在能打开该站点的浏览器按 F12 → 网络 → 复制整行 Cookie，到『站点会话』卡片导入即可）"


class SubscriptionError(RuntimeError):
    pass


class SubscriptionService:
    def __init__(self, store, guard, mikan, save_path_provider):
        self.store = store
        self.guard = guard
        self.mikan = mikan
        self.save_path_provider = save_path_provider  # () -> 全局默认下载目录

    # ---- 管理 ----

    def add(self, rss_url: str, title: str = "", save_path: str = "") -> dict:
        """添加订阅并立即检查一轮（粘贴即下载）。被 CF 拦截时仍保存、返回 blocked 摘要。"""
        rss_url = (rss_url or "").strip()
        if not rss_url.lower().startswith(("http://", "https://")):
            raise SubscriptionError("RSS 链接必须以 http(s):// 开头")
        try:
            feed_title, episodes = self.mikan.fetch_feed(rss_url)
        except CloudflareBlocked as exc:
            sub_id = self.store.sub_add(rss_url, title.strip(), save_path.strip())
            self.store.sub_mark_checked(sub_id, error=str(exc))
            return {
                "id": sub_id,
                "title": title.strip(),
                "total": 0,
                "started": 0,
                "queued": 0,
                "errors": [],
                "blocked": True,
                "detail": str(exc) + COOKIE_HINT,
            }
        except Exception as exc:
            raise SubscriptionError(f"RSS 拉取失败：{exc}") from exc

        sub_id = self.store.sub_add(rss_url, title.strip() or feed_title, save_path.strip())
        summary = {
            "id": sub_id,
            "title": title.strip() or feed_title,
            "total": len(episodes),
            "started": 0,
            "queued": 0,
            "errors": [],
        }
        self._download_new(
            sub_id, episodes, save_path.strip() or None, summary, strict_directory=True
        )
        return summary

    def check_one(self, sub_id: int) -> dict:
        sub = self.store.sub_get(sub_id)
        if sub is None:
            raise SubscriptionError(f"订阅不存在: {sub_id}")
        summary = {"id": sub_id, "title": sub["title"], "started": 0, "queued": 0, "errors": []}
        try:
            _title, episodes = self.mikan.fetch_feed(sub["rss_url"])
        except CloudflareBlocked as exc:
            self.store.sub_mark_checked(sub_id, error=str(exc))
            summary["blocked"] = True
            summary["detail"] = str(exc) + COOKIE_HINT
            return summary
        except Exception as exc:
            self.store.sub_mark_checked(sub_id, error=f"拉取失败：{exc}")
            summary["detail"] = f"拉取失败：{exc}"
            return summary
        self._download_new(sub_id, episodes, sub["save_path"] or None, summary)
        return summary

    def check_all(self) -> dict:
        """调度器轮询入口：逐个启用中的订阅检查；被 CF 拦截则停止本轮避免连环触发。"""
        summary = {"checked": 0, "started": 0, "blocked": 0}
        for sub in self.store.sub_list(enabled_only=True):
            result = self.check_one(sub["id"])
            summary["checked"] += 1
            summary["started"] += result.get("started", 0)
            if result.get("blocked"):
                summary["blocked"] += 1
                break
        return summary

    # ---- 内部 ----

    def _download_new(self, sub_id, episodes, save_path_override, summary, strict_directory=False):
        fresh = [ep for ep in episodes if not self.store.seen(ep.guid)]
        target = save_path_override or self.save_path_provider()
        if fresh and not target:
            message = "未指定下载目录：请先在『下载目录』卡片保存默认目录，或添加订阅时填写本次目录"
            if strict_directory:
                raise SubscriptionError(message)
            summary["errors"].append(message)
            self.store.sub_mark_checked(sub_id, error=message)
            return
        for ep in sorted(fresh, key=lambda e: e.published):
            try:
                raw = self.mikan.download_torrent(ep)
                decision = self.guard.admit(raw, save_path=target)
                self.store.mark_seen(ep.guid, title=ep.title, added_at=time.time())
                summary["started" if decision.start else "queued"] += 1
            except CloudflareBlocked as exc:
                self.store.sub_mark_checked(sub_id, error=str(exc))
                summary["blocked"] = True
                summary["detail"] = str(exc) + COOKIE_HINT
                return
            except (EngineError, RuntimeError, ValueError) as exc:
                summary["errors"].append(f"{ep.title}: {exc}")
        self.store.sub_mark_checked(sub_id)
