"""本地状态库（SQLite）：RSS 条目去重 + 订阅管理 + 种子下载/上传字节按自然日记账。"""

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Usage:
    """某自然日的累计用量（字节）。"""

    down: int = 0
    up: int = 0


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # API 线程池与调度器线程会并发访问，显式允许跨线程并用锁串行化
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen(
                guid TEXT PRIMARY KEY,
                torrent_hash TEXT,
                title TEXT,
                added_at REAL
            );
            CREATE TABLE IF NOT EXISTS ledger(
                hash TEXT PRIMARY KEY,
                size INTEGER,
                downloaded INTEGER,
                uploaded INTEGER
            );
            CREATE TABLE IF NOT EXISTS daily(day TEXT PRIMARY KEY, used INTEGER, up_used INTEGER);
            CREATE TABLE IF NOT EXISTS subscriptions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rss_url TEXT UNIQUE,
                title TEXT DEFAULT '',
                save_path TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                added_at REAL DEFAULT 0,
                last_checked REAL DEFAULT 0,
                last_error TEXT DEFAULT '',
                deleted_at REAL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS mirrors(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                base_url TEXT UNIQUE,
                enabled INTEGER DEFAULT 1,
                added_at REAL DEFAULT 0,
                last_checked REAL DEFAULT 0,
                last_ok REAL DEFAULT 0,
                last_error TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS sub_episodes(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sub_id INTEGER,
                guid TEXT UNIQUE,
                sha TEXT,
                title TEXT DEFAULT '',
                state TEXT DEFAULT 'added',
                added_at REAL DEFAULT 0,
                done_at REAL DEFAULT 0
            );
            """
        )
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        """旧版本库自动补列（V1 数据无损升级）。"""
        self._ensure_column("ledger", "uploaded", "INTEGER DEFAULT 0")
        self._ensure_column("daily", "up_used", "INTEGER DEFAULT 0")
        self._ensure_column("subscriptions", "deleted_at", "REAL DEFAULT 0")
        self._ensure_column("subscriptions", "mirror_host", "TEXT DEFAULT ''")

    # ---- 镜像站 ----

    def mirror_add(self, base_url: str) -> int:
        with self._lock:
            row = self.conn.execute(
                "SELECT id FROM mirrors WHERE base_url=?", (base_url,)
            ).fetchone()
            if row is not None:
                return row["id"]
            cur = self.conn.execute(
                "INSERT INTO mirrors(base_url, added_at) VALUES (?,?)", (base_url, time.time())
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def mirror_list(self):
        with self._lock:
            return [
                dict(r)
                for r in self.conn.execute(
                    "SELECT * FROM mirrors WHERE enabled=1 "
                    "ORDER BY last_ok DESC, id"
                ).fetchall()
            ]

    def mirror_remove(self, mirror_id: int):
        with self._lock:
            self.conn.execute("DELETE FROM mirrors WHERE id=?", (mirror_id,))
            self.conn.commit()

    def mirror_mark(self, mirror_id: int, ok: bool, error: str = ""):
        with self._lock:
            self.conn.execute(
                "UPDATE mirrors SET last_checked=?, last_ok=?, last_error=? WHERE id=?",
                (time.time(), time.time() if ok else 0, error, mirror_id),
            )
            self.conn.commit()

    def mirror_hosts(self):
        """启用中的镜像主机名（不含 scheme），按 最近可用优先 排序。"""
        with self._lock:
            return [
                r["base_url"].split("://", 1)[1]
                for r in self.conn.execute(
                    "SELECT base_url FROM mirrors WHERE enabled=1 "
                    "ORDER BY (last_ok > 0) DESC, id"
                ).fetchall()
            ]

    def _ensure_column(self, table, column, ddl):
        columns = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    # ---- 去重 ----

    def seen(self, guid):
        with self._lock:
            row = self.conn.execute("SELECT 1 FROM seen WHERE guid=?", (guid,)).fetchone()
            return row is not None

    def mark_seen(self, guid, torrent_hash="", title="", added_at=0.0):
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO seen VALUES (?,?,?,?)",
                (guid, torrent_hash, title, added_at),
            )
            self.conn.commit()

    # ---- 订阅（RSS 链接） ----

    def sub_add(self, rss_path: str, title: str = "", save_path: str = "", mirror_host: str = "") -> int:
        """按 RSS 路径 upsert（域名无关：同一订阅在不同镜像视为同一条）。
        重复添加视为更新标题/目录并重新启用（含从已删除恢复）。返回订阅 id。"""
        with self._lock:
            row = self.conn.execute(
                "SELECT id FROM subscriptions WHERE rss_url=?", (rss_path,)
            ).fetchone()
            if row is not None:
                self.conn.execute(
                    "UPDATE subscriptions SET title=?, save_path=?, enabled=1, deleted_at=0, "
                    "mirror_host=? WHERE id=?",
                    (title, save_path, mirror_host, row["id"]),
                )
                self.conn.commit()
                return row["id"]
            cur = self.conn.execute(
                "INSERT INTO subscriptions(rss_url, title, save_path, enabled, added_at, mirror_host) "
                "VALUES (?,?,?,?,?,?)",
                (rss_path, title, save_path, 1, time.time(), mirror_host),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def sub_set_mirror(self, sub_id: int, mirror_host: str):
        with self._lock:
            self.conn.execute(
                "UPDATE subscriptions SET mirror_host=? WHERE id=?", (mirror_host, sub_id)
            )
            self.conn.commit()

    def sub_set_rss(self, sub_id: int, rss_path: str, mirror_host: str | None = None):
        """旧数据迁移：把完整 URL 改写为「路径 + 最近可用镜像」形式。"""
        with self._lock:
            if mirror_host is None:
                self.conn.execute(
                    "UPDATE subscriptions SET rss_url=? WHERE id=?", (rss_path, sub_id)
                )
            else:
                self.conn.execute(
                    "UPDATE subscriptions SET rss_url=?, mirror_host=? WHERE id=?",
                    (rss_path, mirror_host, sub_id),
                )
            self.conn.commit()

    def _sub_dict(self, row):
        return dict(row)

    def sub_list(self, deleted: bool = False, enabled_only: bool = False):
        """deleted=False 只列在用订阅；True 只列已删除；None 列全部。"""
        with self._lock:
            sql = "SELECT * FROM subscriptions"
            conditions = []
            if deleted is True:
                conditions.append("deleted_at > 0")
            elif deleted is False:
                conditions.append("deleted_at = 0")
            if enabled_only:
                conditions.append("enabled = 1")
            if conditions:
                sql += " WHERE " + " AND ".join(conditions)
            sql += " ORDER BY id"
            return [dict(r) for r in self.conn.execute(sql).fetchall()]

    def sub_get(self, sub_id: int):
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM subscriptions WHERE id=?", (sub_id,)
            ).fetchone()
            return self._sub_dict(row) if row is not None else None

    def sub_delete(self, sub_id: int):
        with self._lock:
            self.conn.execute("DELETE FROM subscriptions WHERE id=?", (sub_id,))
            self.conn.commit()

    def sub_rename(self, sub_id: int, title: str):
        with self._lock:
            self.conn.execute(
                "UPDATE subscriptions SET title=? WHERE id=?", (title, sub_id)
            )
            self.conn.commit()

    def sub_set_save_path(self, sub_id: int, save_path: str):
        with self._lock:
            self.conn.execute(
                "UPDATE subscriptions SET save_path=? WHERE id=?", (save_path, sub_id)
            )
            self.conn.commit()

    def sub_soft_delete(self, sub_id: int):
        """软删除：订阅移入「已删除」，集数追踪保留，可恢复。"""
        with self._lock:
            self.conn.execute(
                "UPDATE subscriptions SET deleted_at=?, enabled=0 WHERE id=?",
                (time.time(), sub_id),
            )
            self.conn.commit()

    def sub_restore(self, sub_id: int):
        with self._lock:
            self.conn.execute(
                "UPDATE subscriptions SET deleted_at=0, enabled=1 WHERE id=?", (sub_id,)
            )
            self.conn.commit()

    def sub_purge(self, sub_id: int):
        """彻底删除（连同其集数追踪记录）。"""
        with self._lock:
            self.conn.execute("DELETE FROM sub_episodes WHERE sub_id=?", (sub_id,))
            self.conn.execute("DELETE FROM subscriptions WHERE id=?", (sub_id,))
            self.conn.commit()

    # ---- 订阅集数追踪（下载完成才算「已见」，中途丢失会自动补拉） ----

    def episode_add(self, sub_id: int, guid: str, sha: str, title: str = ""):
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO sub_episodes(sub_id, guid, sha, title, state, added_at) "
                "VALUES (?,?,?,?, 'added', ?)",
                (sub_id, guid, sha, title, time.time()),
            )
            self.conn.commit()

    def episode_exists(self, guid: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM sub_episodes WHERE guid=?", (guid,)
            ).fetchone()
            return row is not None

    def episode_pending(self, sub_id: int):
        with self._lock:
            return [
                dict(r)
                for r in self.conn.execute(
                    "SELECT * FROM sub_episodes WHERE sub_id=? AND state='added'", (sub_id,)
                ).fetchall()
            ]

    def episodes_all(self, sub_id: int):
        with self._lock:
            return [
                dict(r)
                for r in self.conn.execute(
                    "SELECT * FROM sub_episodes WHERE sub_id=?", (sub_id,)
                ).fetchall()
            ]

    def episode_mark_done(self, guid: str):
        with self._lock:
            self.conn.execute(
                "UPDATE sub_episodes SET state='done', done_at=? WHERE guid=?",
                (time.time(), guid),
            )
            self.conn.commit()

    def sub_set_enabled(self, sub_id: int, enabled: bool):
        with self._lock:
            self.conn.execute(
                "UPDATE subscriptions SET enabled=? WHERE id=?", (1 if enabled else 0, sub_id)
            )
            self.conn.commit()

    def sub_mark_checked(self, sub_id: int, error: str | None = None):
        with self._lock:
            self.conn.execute(
                "UPDATE subscriptions SET last_checked=?, last_error=? WHERE id=?",
                (time.time(), error or "", sub_id),
            )
            self.conn.commit()

    # ---- 记账 ----

    def attribute_all(self, torrents, day):
        """把种子的下载/上传增量归集到发生当日，返回当日用量 Usage。"""
        with self._lock:
            return self._attribute_all_locked(torrents, day)

    def _attribute_all_locked(self, torrents, day):
        used = Usage(down=self._daily_get(day, "used"), up=self._daily_get(day, "up_used"))
        for t in torrents:
            downloaded = int(t.get("downloaded") or 0)
            uploaded = int(t.get("uploaded") or 0)
            size = int(t.get("size") or 0)
            row = self.conn.execute(
                "SELECT downloaded, uploaded FROM ledger WHERE hash=?", (t["hash"],)
            ).fetchone()
            if row is None:
                down_delta, up_delta = downloaded, uploaded
                self.conn.execute(
                    "INSERT INTO ledger VALUES (?,?,?,?)", (t["hash"], size, downloaded, uploaded)
                )
            else:
                down_delta = max(0, downloaded - row[0])
                up_delta = max(0, uploaded - row[1])
                self.conn.execute(
                    "UPDATE ledger SET downloaded=?, uploaded=?, size=? WHERE hash=?",
                    (downloaded, uploaded, size, t["hash"]),
                )
            if down_delta:
                self._daily_add(day, "used", down_delta)
                used.down += down_delta
            if up_delta:
                self._daily_add(day, "up_used", up_delta)
                used.up += up_delta
        self.conn.commit()
        return used

    def _daily_get(self, day, column):
        row = self.conn.execute(
            f"SELECT {column} FROM daily WHERE day=?", (day,)
        ).fetchone()
        return row[0] if row and row[0] is not None else 0

    def _daily_add(self, day, column, amount):
        self.conn.execute(
            f"INSERT INTO daily(day, {column}) VALUES(?, ?) "
            f"ON CONFLICT(day) DO UPDATE SET {column} = COALESCE({column}, 0) + excluded.{column}",
            (day, amount),
        )
