"""SQLite tarix — snapshot'lar va delta hisoblash."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at    TEXT    NOT NULL          -- ISO-8601, Asia/Tashkent
);

CREATE TABLE IF NOT EXISTS votes (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    project_id  TEXT    NOT NULL,
    quarter     TEXT    NOT NULL,
    votes       INTEGER NOT NULL,
    rank        INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, project_id)
);

CREATE INDEX IF NOT EXISTS idx_votes_project ON votes(project_id);

CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class Delta:
    """Bir loyihaning ma'lum davr ichidagi o'sishi."""
    d30: int | None      # oxirgi ~interval (30 daq)
    d24h: int | None     # oxirgi 24 soat


class Storage:
    def __init__(self, path: Path):
        self.path = path
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    # ---------- yozish ----------

    def save_snapshot(self, taken_at: datetime, rows: list) -> int:
        """rows: ovoz bo'yicha saralangan Initiative ro'yxati."""
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO snapshots (taken_at) VALUES (?)", (taken_at.isoformat(),)
            )
            sid = cur.lastrowid
            conn.executemany(
                "INSERT INTO votes (snapshot_id, project_id, quarter, votes, rank) "
                "VALUES (?, ?, ?, ?, ?)",
                [(sid, r.project_id, r.quarter, r.votes, i + 1) for i, r in enumerate(rows)],
            )
            return sid

    # ---------- o'qish ----------

    def _votes_at_or_before(self, moment: datetime) -> dict[str, int]:
        """Berilgan vaqtdan oldingi eng yaqin snapshot'dagi ovozlar."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM snapshots WHERE taken_at <= ? ORDER BY taken_at DESC LIMIT 1",
                (moment.isoformat(),),
            ).fetchone()
            if row is None:
                return {}
            return {
                r["project_id"]: r["votes"]
                for r in conn.execute(
                    "SELECT project_id, votes FROM votes WHERE snapshot_id = ?", (row["id"],)
                )
            }

    def previous_votes(self, before_snapshot_id: int) -> dict[str, int]:
        """Joriy snapshot'dan oldingi snapshot (delta-30 uchun)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM snapshots WHERE id < ? ORDER BY id DESC LIMIT 1",
                (before_snapshot_id,),
            ).fetchone()
            if row is None:
                return {}
            return {
                r["project_id"]: r["votes"]
                for r in conn.execute(
                    "SELECT project_id, votes FROM votes WHERE snapshot_id = ?", (row["id"],)
                )
            }

    def deltas(self, snapshot_id: int, now: datetime, current: dict[str, int]) -> dict[str, Delta]:
        prev = self.previous_votes(snapshot_id)
        day_ago = self._votes_at_or_before(now - timedelta(hours=24))
        out: dict[str, Delta] = {}
        for pid, votes in current.items():
            out[pid] = Delta(
                d30=votes - prev[pid] if pid in prev else None,
                d24h=votes - day_ago[pid] if pid in day_ago else None,
            )
        return out

    def votes_between(self, start: datetime, end: datetime) -> tuple[dict[str, int], dict[str, int]]:
        """Kechasi o'sishini hisoblash uchun: (start dagi, end dagi) ovozlar."""
        return self._votes_at_or_before(start), self._votes_at_or_before(end)

    def last_rank(self, project_id: str, before_snapshot_id: int) -> int | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT v.rank FROM votes v WHERE v.project_id = ? AND v.snapshot_id < ? "
                "ORDER BY v.snapshot_id DESC LIMIT 1",
                (project_id, before_snapshot_id),
            ).fetchone()
            return row["rank"] if row else None

    # ---------- kalit-qiymat holati ----------

    def get_state(self, key: str, default: str | None = None) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def prune(self, keep: int = 60) -> int:
        """
        Faqat oxirgi `keep` ta snapshot qoldiriladi.

        24 soatlik delta uchun 48 tasi yetarli (30 daqiqalik interval).
        Bu, ayniqsa, GitHub Actions'da muhim: baza kesh orqali tashiladi,
        cheksiz o'sishi kerak emas.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM snapshots ORDER BY id DESC LIMIT 1 OFFSET ?", (keep,)
            ).fetchone()
            if row is None:
                return 0
            cutoff = row["id"]
            deleted = conn.execute(
                "DELETE FROM votes WHERE snapshot_id <= ?", (cutoff,)
            ).rowcount
            conn.execute("DELETE FROM snapshots WHERE id <= ?", (cutoff,))

        # VACUUM tranzaksiya ichida ishlamaydi — alohida ulanishda
        vacuum = sqlite3.connect(self.path)
        try:
            vacuum.isolation_level = None
            vacuum.execute("VACUUM")
        finally:
            vacuum.close()
        return deleted

    def snapshot_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM snapshots").fetchone()["c"]
