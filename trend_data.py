"""
Trend ma'lumotini tayyorlash: har bir loyihaning bir necha vaqtdagi holati.

Bitta "oxirgi yarim soat" raqami yetarli emas edi — u ovozlar to'p-to'p
kelganda chalkashtiradi. Bu yerda bir necha nuqta olinadi (10, 5, 2 soat
oldin), shunda haqiqiy harakat ko'rinadi.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from config import CONFIG
from renderer import to_latin
from trend_render import TrendRow

#: Necha soat oldingi holatlar ko'rsatiladi (eskidan yangiga)
POINTS = (10, 5, 2)


@dataclass
class Snap:
    votes: int
    rank: int
    at: datetime


def _at(db, moment: datetime) -> tuple[dict[str, Snap], datetime | None]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, taken_at FROM snapshots WHERE taken_at <= ? "
            "ORDER BY taken_at DESC LIMIT 1", (moment.isoformat(),)).fetchone()
        if row is None:
            return {}, None
        taken = datetime.fromisoformat(row["taken_at"])
        return ({r["project_id"]: Snap(r["votes"], r["rank"], taken)
                 for r in conn.execute(
                     "SELECT project_id, votes, rank FROM votes "
                     "WHERE snapshot_id = ?", (row["id"],))}, taken)
    finally:
        conn.close()


def collect(items, now: datetime, winners: int | None,
            top: int = 20, points=POINTS, mark_second: bool = True):
    """
    Qaytaradi: (rows, labels, meta)

    rows   — trend_render.TrendRow ro'yxati
    labels — ustun sarlavhalari ("10 soat", ...)
    meta   — {"pid": {"past": [...], "now": int, "rate": float, "rank": int}}
             matnli xabar tuzish uchun
    """
    # Tarix yupqa bo'lsa bir necha so'rov bitta snapshotga tushadi —
    # takroriy ustun foydasiz, shuning uchun yagonalashtiramiz.
    history = []
    seen: set[str] = set()
    for h in points:
        data, taken = _at(CONFIG.db_path, now - timedelta(hours=h))
        key = taken.isoformat() if taken else "yo'q"
        if key in seen:
            continue
        seen.add(key)
        history.append((h, data, taken))

    # Sur'at eng uzoq mavjud nuqtaga nisbatan hisoblanadi — u eng barqaror
    base = next((d for _, d, t in history if d), {})
    base_at = next((t for _, d, t in history if d), None)
    hours = ((now - base_at).total_seconds() / 3600) if base_at else 0

    # Sarlavha so'ralgan emas, HAQIQIY topilgan snapshot vaqtini ko'rsatadi:
    # tarix yupqa bo'lsa "2 soat" deb yozib qo'yish yolg'on bo'lardi.
    labels = []
    for h, data, taken in history:
        if taken is None:
            labels.append("—")
        else:
            real = (now - taken).total_seconds() / 3600
            labels.append(f"{real:.0f} soat" if real >= 1 else f"{real*60:.0f} daq")
    rows, meta = [], {}

    shown = list(items[:top])
    wanted = ((CONFIG.project_id, CONFIG.second_project_id) if mark_second
              else (CONFIG.project_id,))
    for pid in wanted:
        if pid and not any(it.project_id == pid for it in shown):
            extra = next((it for it in items if it.project_id == pid), None)
            if extra:
                shown.append(extra)

    index = {it.project_id: i + 1 for i, it in enumerate(items)}
    ours = next((it for it in items if it.project_id == CONFIG.project_id), None)
    our_votes = ours.votes if ours else 0
    for it in shown:
        rank = index[it.project_id]
        past = [d.get(it.project_id).votes if d.get(it.project_id) else None
                for _, d, _ in history]
        b = base.get(it.project_id)
        rate = ((it.votes - b.votes) / hours) if (b and hours) else 0.0
        rows.append(TrendRow(
            rank=rank,
            name=to_latin(it.quarter)[:20],
            past=past,
            diff=None if it.project_id == CONFIG.project_id else our_votes - it.votes,
            now=it.votes,
            rate=rate,
            is_us=it.project_id == CONFIG.project_id,
            is_us2=mark_second and it.project_id == CONFIG.second_project_id,
            cutoff_after=bool(winners) and rank == winners,
        ))
        meta[it.project_id] = {"past": past, "now": it.votes,
                               "rate": rate, "rank": rank,
                               "name": to_latin(it.quarter)}

    rows.sort(key=lambda r: r.rank)
    return rows, labels, meta
