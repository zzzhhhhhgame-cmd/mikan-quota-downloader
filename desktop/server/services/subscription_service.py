"""RSS 链接订阅服务：粘贴任意 Mikan RSS 链接即可自动追更，无需站点账号。

关于"RSS 链接内容会不会更新"：番剧 RSS 是滚动窗口（只保留最近若干条），
但本服务每次轮询都拉全量条目并去重——新集出现在源里就会被下载，源滚动不影响订阅。

集数生命周期（让"删了订阅/丢了任务就永远不补下"成为过去）：
1. 新集下载入队时登记到 sub_episodes（state=added），并把 .torrent 存档到本地
   `data/torrents/<sha>.torrent`；
2. 每轮调度先执行 reconcile：集数任务已完成 → 标记 done + 写入 seen（永久去重）；
   集数任务在引擎里丢了（手动删除/应用重启）→ 从本地存档自动重新入队；
3. 删除订阅是软删除（可在「已删除」列表恢复），集数追踪保留；
   「彻底删除」才连同追踪一起清掉。

被 Cloudflare 拦截时：订阅仍会保存，界面提示导入 Cookie（无需登录账号）。
"""

from __future__ import annotations

import time
from pathlib import Path

from mqd.session import CloudflareBlocked
from mqd.torrents import infohash_from_bytes

from ..engine.base import EngineError, TaskState

COOKIE_HINT = "（无需登录账号：在能打开该站点的浏览器按 F12 → 网络 → 复制整行 Cookie，到『站点 Cookie』卡片导入即可）"


class SubscriptionError(RuntimeError):
    pass


class SubscriptionService:
    def __init__(self, store, guard, mikan, save_path_provider, torrent_dir="data/torrents"):
        self.store = store
        self.guard = guard
        self.mikan = mikan
        self.save_path_provider = save_path_provider  # () -> 全局默认下载目录
        self.torrent_dir = torrent_dir  # .torrent 存档目录（断点补拉用）

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

    def rename(self, sub_id: int, title: str) -> dict:
        sub = self.store.sub_get(sub_id)
        if sub is None:
            raise SubscriptionError(f"订阅不存在: {sub_id}")
        title = (title or "").strip()
        if not title:
            raise SubscriptionError("名称不能为空")
        self.store.sub_rename(sub_id, title)
        return self.store.sub_get(sub_id)

    def delete(self, sub_id: int) -> None:
        """软删除：移入「已删除」，可在订阅页恢复。"""
        sub = self.store.sub_get(sub_id)
        if sub is None:
            raise SubscriptionError(f"订阅不存在: {sub_id}")
        self.store.sub_soft_delete(sub_id)

    def restore(self, sub_id: int) -> dict:
        sub = self.store.sub_get(sub_id)
        if sub is None:
            raise SubscriptionError(f"订阅不存在: {sub_id}")
        self.store.sub_restore(sub_id)
        return self.store.sub_get(sub_id)

    # ---- 检查与补拉 ----

    def check_one(self, sub_id: int) -> dict:
        sub = self.store.sub_get(sub_id)
        if sub is None:
            raise SubscriptionError(f"订阅不存在: {sub_id}")
        if sub["deleted_at"]:
            raise SubscriptionError("订阅已删除（可在订阅页的「已删除」列表恢复）")
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
        """RSS 轮询：逐个启用中的订阅检查；被 CF 拦截则停止本轮避免连环触发。"""
        summary = {"checked": 0, "started": 0, "blocked": 0}
        for sub in self.store.sub_list(enabled_only=True):
            result = self.check_one(sub["id"])
            summary["checked"] += 1
            summary["started"] += result.get("started", 0)
            if result.get("blocked"):
                summary["blocked"] += 1
                break
        return summary

    def reconcile_all(self) -> dict:
        """本地补拉（不联网）：已完成→标记；任务丢失→从本地 .torrent 存档重新入队。"""
        readded = 0
        for sub in self.store.sub_list(enabled_only=True):
            readded += self.reconcile(sub)
        return {"readded": readded}

    def periodic(self) -> dict:
        """调度器每轮入口：先本地补拉（无需网络），再拉 RSS 检查更新。"""
        result = self.reconcile_all()
        result.update(self.check_all())
        return result

    def reconcile(self, sub: dict) -> int:
        fixed = 0
        for ep in self.store.episode_pending(sub["id"]):
            tasks = [t for t in self.guard.engine.list() if t.sha == ep["sha"]]
            if tasks and tasks[0].state in (TaskState.COMPLETED, TaskState.SEEDING):
                # 下载完成：标记 done 并写入 seen 永久去重
                self.store.episode_mark_done(ep["guid"])
                self.store.mark_seen(ep["guid"], title=ep["title"], added_at=time.time())
                continue
            if tasks:
                continue  # 还在引擎里（下载中/排队中），无需处理
            archive = Path(self.torrent_dir) / f"{ep['sha']}.torrent"
            if not archive.exists():
                continue
            try:
                self.guard.admit(archive.read_bytes(), save_path=sub["save_path"] or self.save_path_provider())
                fixed += 1
            except (EngineError, RuntimeError, ValueError):
                continue  # 文件损坏等，下轮再试
        return fixed

    # ---- 内部 ----

    def _download_new(self, sub_id, episodes, save_path_override, summary, strict_directory=False):
        fresh = [
            ep
            for ep in episodes
            if not self.store.seen(ep.guid) and not self.store.episode_exists(ep.guid)
        ]
        target = save_path_override or self.save_path_provider()
        if fresh and not target:
            message = "未指定下载目录：请先在设置中保存默认下载目录，或添加订阅时填写本次目录"
            if strict_directory:
                raise SubscriptionError(message)
            summary["errors"].append(message)
            self.store.sub_mark_checked(sub_id, error=message)
            return
        for ep in sorted(fresh, key=lambda e: e.published):
            try:
                raw = self.mikan.download_torrent(ep)
                decision = self.guard.admit(raw, save_path=target)
                sha = infohash_from_bytes(raw)
                self.store.episode_add(sub_id, ep.guid, sha, ep.title)
                self._save_torrent(sha, raw)  # 本地存档，供任务丢失后补拉
                summary["started" if decision.start else "queued"] += 1
            except CloudflareBlocked as exc:
                self.store.sub_mark_checked(sub_id, error=str(exc))
                summary["blocked"] = True
                summary["detail"] = str(exc) + COOKIE_HINT
                return
            except (EngineError, RuntimeError, ValueError) as exc:
                summary["errors"].append(f"{ep.title}: {exc}")
        self.store.sub_mark_checked(sub_id)

    def _save_torrent(self, sha: str, raw: bytes):
        try:
            path = Path(self.torrent_dir) / f"{sha}.torrent"
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(raw)
        except OSError:
            pass  # 存档失败只影响断点补拉，不阻断主流程
