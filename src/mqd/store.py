"""本地状态库（SQLite）：RSS 条目去重 + 种子下载字节按自然日记账。"""

import sqlite3
from pathlib import Path


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
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
                downloaded INTEGER
            );
            CREATE TABLE IF NOT EXISTS daily(day TEXT PRIMARY KEY, used INTEGER);
            """
        )
        self.conn.commit()

    # ---- 去重 ----

    def seen(self, guid):
        row = self.conn.execute("SELECT 1 FROM seen WHERE guid=?", (guid,)).fetchone()
        return row is not None

    def mark_seen(self, guid, torrent_hash="", title="", added_at=0.0):
        self.conn.execute(
            "INSERT OR IGNORE INTO seen VALUES (?,?,?,?)", (guid, torrent_hash, title, added_at)
        )
        self.conn.commit()

    # ---- 记账 ----

    def attribute_all(self, torrents, day):
        """把种子的下载增量归集到发生当日，返回当日累计用量。

        首次见到的种子若已带有下载量（如换库重跑），该部分计入今天。
        """
        used = self._daily_get(day)
        for t in torrents:
            downloaded = int(t.get("downloaded") or 0)
            size = int(t.get("size") or 0)
            row = self.conn.execute(
                "SELECT downloaded FROM ledger WHERE hash=?", (t["hash"],)
            ).fetchone()
            if row is None:
                if downloaded > 0:
                    self._daily_add(day, downloaded)
                    used += downloaded
                self.conn.execute(
                    "INSERT INTO ledger VALUES (?,?,?)", (t["hash"], size, downloaded)
                )
            elif downloaded > row[0]:
                delta = downloaded - row[0]
                self._daily_add(day, delta)
                used += delta
                self.conn.execute(
                    "UPDATE ledger SET downloaded=?, size=? WHERE hash=?",
                    (downloaded, size, t["hash"]),
                )
        self.conn.commit()
        return used

    def _daily_get(self, day):
        row = self.conn.execute("SELECT used FROM daily WHERE day=?", (day,)).fetchone()
        return row[0] if row else 0

    def _daily_add(self, day, amount):
        self.conn.execute(
            "INSERT INTO daily(day, used) VALUES(?, ?) "
            "ON CONFLICT(day) DO UPDATE SET used = used + excluded.used",
            (day, amount),
        )
