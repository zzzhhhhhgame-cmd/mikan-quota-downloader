"""RSS 链接订阅服务：粘贴任意 Mikan RSS 链接即可自动追更，无需站点账号。

镜像域名无关：粘贴的链接会提取出域名无关的「路径」（/RSS/…），拉取时按镜像
优先级依次尝试（见 mirrors.py），成功的域名记录到订阅，下次优先使用；某个镜像
失效后自动切换到下一个，订阅不会因单个域名失效而停更。

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

import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from mqd.session import CloudflareBlocked
from mqd.torrents import infohash_from_bytes

from ..engine.base import EngineError, TaskState

COOKIE_HINT = "（无需登录账号：在能打开该站点的浏览器按 F12 → 网络 → 复制整行 Cookie，到『站点 Cookie』卡片导入即可）"


class SubscriptionError(RuntimeError):
    pass


class SubscriptionService:
    def __init__(self, store, guard, mikan, save_path_provider, torrent_dir="data/torrents", mirrors=None):
        self.store = store
        self.guard = guard
        self.mikan = mikan
        self.save_path_provider = save_path_provider  # () -> 全局默认下载目录
        self.torrent_dir = torrent_dir  # .torrent 存档目录（断点补拉用）
        self.mirrors = mirrors  # MirrorService；None=不做镜像故障转移（旧测试路径）

    # ---- 管理 ----

    def migrate_legacy_urls(self) -> int:
        """把旧版存的完整 RSS URL 改写为「路径」形式（域名无关订阅）。"""
        fixed = 0
        for sub in self.store.sub_list(deleted=None):
            url = sub["rss_url"]
            if url.startswith("/"):
                continue
            parts = urlsplit(url)
            path = parts.path + ("?" + parts.query if parts.query else "")
            if path.startswith("/"):
                self.store.sub_set_rss(sub["id"], path)
                fixed += 1
        return fixed

    @staticmethod
    def _rss_path(pasted: str) -> str:
        """从粘贴的完整链接提取域名无关的路径（自动替换镜像域名的关键）。"""
        parts = urlsplit(pasted)
        path = parts.path + ("?" + parts.query if parts.query else "")
        if not path.lower().startswith(("/rss/", "/feed/")):
            raise SubscriptionError(
                "链接看起来不是 Mikan 订阅 RSS：应以 /RSS/ 或 /Feed/ 开头"
                "（例如 https://任何镜像域名/RSS/Bangumi?bangumiId=…&subgroupid=…）"
            )
        return path

    @staticmethod
    def _clean_title(raw: str) -> str:
        """去掉订阅源标题里的站点前缀："Mikan Project - 尼古喵喵" → "尼古喵喵"。"""
        title = (raw or "").strip()
        title = re.sub(
            r"^(mikan\s*project|蜜柑计划|mikan)\s*[-–—:：]\s*", "", title, flags=re.IGNORECASE
        )
        return title.strip()

    @staticmethod
    def _safe_folder(title: str) -> str:
        """番剧名 → 合法的文件夹名（替换文件系统非法字符）。"""
        folder = re.sub(r'[\\/:*?"<>|]', " ", (title or "").strip())
        folder = re.sub(r"\s+", " ", folder).strip().strip(".")
        return folder[:80] or "未命名番剧"

    def _default_sub_dir(self, title: str) -> str:
        """番剧目录规则：<默认下载目录>/<番剧名>；未设置默认目录时返回空。"""
        default_dir = self.save_path_provider()
        if not default_dir:
            return ""
        return os.path.join(default_dir, self._safe_folder(title))

    def _candidate_urls(self, rss_path: str, preferred_host: str = ""):
        """按镜像优先级构造候选 RSS 地址：用户给的域名优先，其余按最近可用排序。"""
        if self.mirrors is None:
            return [rss_path if "://" in rss_path else "https://mikan.tangbai.cc" + rss_path]
        ordered = self.mirrors.ordered_hosts()
        if preferred_host:
            if preferred_host in ordered:
                ordered.remove(preferred_host)
            ordered.insert(0, preferred_host)
        return [f"https://{host}{rss_path}" for host in ordered]

    def _fetch_feed_failover(self, rss_path: str, preferred_host: str = ""):
        """按镜像优先级依次尝试拉取 RSS，返回 (订阅源标题, 条目, 成功的域名)。"""
        candidates = self._candidate_urls(rss_path, preferred_host)
        last_exc: Exception | None = None
        blocked = False
        for url in candidates:
            try:
                feed_title, episodes = self.mikan.fetch_feed(url)
                host = urlsplit(url).netloc
                return feed_title, episodes, host
            except CloudflareBlocked as exc:
                last_exc, blocked = exc, True  # 被盾的镜像换下一个继续试
            except Exception as exc:  # 连接失败/404 等同样换下一个
                last_exc = exc
        if blocked and last_exc is None:
            last_exc = CloudflareBlocked("所有镜像均被 Cloudflare 拦截")
        raise last_exc or RuntimeError("所有镜像均无法访问")

    def add(self, rss_url: str, title: str = "", save_path: str = "") -> dict:
        """添加订阅并立即检查一轮（粘贴即下载）。

        粘贴的链接会自动提取路径并把域名替换为可用镜像（用户给的域名优先尝试）。
        被全部镜像拦截时仍保存、返回 blocked 摘要。
        """
        rss_url = (rss_url or "").strip()
        rss_path = self._rss_path(rss_url)
        preferred = urlsplit(rss_url).netloc
        try:
            feed_title, episodes, host = self._fetch_feed_failover(rss_path, preferred)
        except CloudflareBlocked as exc:
            sub_id = self.store.sub_add(rss_path, title.strip(), save_path.strip(), preferred)
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
            raise SubscriptionError(f"所有镜像均拉取失败：{exc}") from exc

        # 名称自动提取（去 "Mikan Project - " 前缀）+ 每番独立目录：<下载目录>/<番剧名>
        title = title.strip() or self._clean_title(feed_title)
        sub_id = self.store.sub_add(rss_path, title, save_path.strip(), host)
        target = save_path.strip() or self._default_sub_dir(title)
        if target:
            self.store.sub_set_save_path(sub_id, target)
        summary = {
            "id": sub_id,
            "title": title,
            "total": len(episodes),
            "started": 0,
            "queued": 0,
            "errors": [],
        }
        self._download_new(
            sub_id, episodes, target or None, summary, strict_directory=True
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
            feed_title, episodes, host = self._fetch_feed_failover(
                sub["rss_url"], sub.get("mirror_host") or ""
            )
            if host != sub.get("mirror_host"):
                self.store.sub_set_mirror(sub_id, host)  # 记住这次成功的镜像，下次优先
        except CloudflareBlocked as exc:
            self.store.sub_mark_checked(sub_id, error=str(exc))
            summary["blocked"] = True
            summary["detail"] = str(exc) + COOKIE_HINT
            return summary
        except Exception as exc:
            self.store.sub_mark_checked(sub_id, error=f"拉取失败：{exc}")
            summary["detail"] = f"拉取失败：{exc}"
            return summary

        # 懒补全：旧订阅缺名称/缺目录的，借这次拉取补上（名称取自 RSS 标题）
        if not sub["title"] and feed_title:
            clean = self._clean_title(feed_title)
            if clean:
                self.store.sub_rename(sub_id, clean)
                sub["title"] = clean
        default_dir = self.save_path_provider()
        if not sub["save_path"] and default_dir and sub["title"]:
            sub_dir = os.path.join(default_dir, self._safe_folder(sub["title"]))
            self.store.sub_set_save_path(sub_id, sub_dir)
            sub["save_path"] = sub_dir

        self._download_new(sub_id, episodes, sub["save_path"] or None, summary)
        return summary

    def check_all(self) -> dict:
        """RSS 轮询：逐个启用中的订阅检查；全部镜像被拦则停止本轮避免连环触发。"""
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

    def organize(self, fetch_titles: bool = False) -> dict:
        """整理订阅与已下载动画：补全番剧名/目录，把引擎任务的文件搬进各番剧文件夹。

        - 目录规则：<默认下载目录>/<番剧名>（手动指定过目录的订阅尊重原设置）；
        - 文件搬迁用引擎 move_storage（任务跟随文件，自动重校验），仅处理仍在引擎里的任务；
        - fetch_titles=True 时会联网拉取 RSS 补全缺失的番剧名（启动时只做离线部分）。
        """
        result = {"renamed": 0, "paths_set": 0, "moved": 0, "skipped": 0}
        default_dir = self.save_path_provider()

        sha_to_sub: dict = {}
        for sub in self.store.sub_list(deleted=None):
            if fetch_titles and not sub["title"]:
                try:
                    feed_title, _eps, _host = self._fetch_feed_failover(
                        sub["rss_url"], sub.get("mirror_host") or ""
                    )
                    clean = self._clean_title(feed_title)
                    if clean:
                        self.store.sub_rename(sub["id"], clean)
                        sub["title"] = clean
                        result["renamed"] += 1
                except Exception:
                    pass  # 拉取失败不阻塞整理，标题留待下次
            if not sub["title"]:
                result["skipped"] += 1
                continue
            if not sub["save_path"] and default_dir:
                sub["save_path"] = os.path.join(default_dir, self._safe_folder(sub["title"]))
                self.store.sub_set_save_path(sub["id"], sub["save_path"])
                result["paths_set"] += 1
            for ep in self.store.episodes_all(sub["id"]):
                sha_to_sub[ep["sha"]] = sub

        for task in self.guard.engine.list():
            sub = sha_to_sub.get(task.sha)
            if sub is None or not sub.get("save_path"):
                continue
            if Path(task.save_path or "").resolve() == Path(sub["save_path"]).resolve():
                continue
            try:
                self.guard.engine.move_storage(task.sha, sub["save_path"])
                result["moved"] += 1
            except EngineError:
                continue
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
