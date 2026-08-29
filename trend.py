"""
Trend tahlili: har bir loyiha nima qilyapti, qaysi biri xavfli.

    python trend.py                # matn hisobot
    python trend.py --hours 10 5   # taqqoslash nuqtalarini o'zgartirish
    python trend.py --no-save      # snapshot yozmasdan (sinov uchun)

Har ishga tushganda joriy holatni bazaga yozadi va oldingi nuqtalar bilan
solishtiradi. Shuning uchun birinchi ishga tushishlarda taqqoslash bo'lmaydi —
tarix to'planishi kerak.

Chiqish matni Telegramga yoki Claude'ga uzatish uchun mo'ljallangan.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from config import CONFIG, TASHKENT
from renderer import fmt_votes, to_latin
from scraper import fetch_budget, fetch_sorted, find_project, winners_count
from storage import Storage

DEADLINE = datetime(2026, 8, 31, 23, 59, tzinfo=TASHKENT)


@dataclass
class Point:
    """Loyihaning ma'lum vaqtdagi holati."""
    votes: int
    rank: int
    at: datetime


def history_at(db, moment: datetime) -> tuple[dict[str, Point], datetime | None]:
    """Berilgan vaqtdan oldingi eng yaqin snapshot."""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, taken_at FROM snapshots WHERE taken_at <= ? "
            "ORDER BY taken_at DESC LIMIT 1", (moment.isoformat(),)
        ).fetchone()
        if row is None:
            return {}, None
        taken = datetime.fromisoformat(row["taken_at"])
        data = {
            r["project_id"]: Point(r["votes"], r["rank"], taken)
            for r in conn.execute(
                "SELECT project_id, votes, rank FROM votes WHERE snapshot_id = ?",
                (row["id"],))
        }
        return data, taken
    finally:
        conn.close()


def rate_label(per_hour: float) -> str:
    if per_hour >= 70:
        return "SHIDDATLI"
    if per_hour >= 35:
        return "tez"
    if per_hour >= 10:
        return "o'rtacha"
    if per_hour >= 2:
        return "sekin"
    return "TO'XTAGAN"


def arrow(now_rank: int, then_rank: int | None) -> str:
    if then_rank is None:
        return "  "
    if now_rank < then_rank:
        return f"^{then_rank - now_rank}"
    if now_rank > then_rank:
        return f"v{now_rank - then_rank}"
    return "= "


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, nargs=2, default=[10, 5],
                    metavar=("UZOQ", "YAQIN"), help="taqqoslash nuqtalari (soat)")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--telegram", action="store_true",
                    help="hisobotni ADMIN_CHAT_ID ga yuborish (guruhlarga emas)")
    args = ap.parse_args()

    now = datetime.now(TASHKENT)
    items = fetch_sorted()
    budget = fetch_budget()
    winners = winners_count(items, budget)

    store = Storage(CONFIG.db_path)
    far, far_at = history_at(CONFIG.db_path, now - timedelta(hours=args.hours[0]))
    near, near_at = history_at(CONFIG.db_path, now - timedelta(hours=args.hours[1]))

    if not args.no_save:
        store.save_snapshot(now, items)
        store.prune(keep=200)

    left_h = max(0.0, (DEADLINE - now).total_seconds() / 3600)
    rank_of = {it.project_id: i + 1 for i, it in enumerate(items)}

    out: list[str] = []
    add = out.append

    add(f"URGUT TUMANI — TREND TAHLILI")
    add(f"Vaqt: {now:%d.%m.%Y %H:%M} | Muddatgacha: {left_h:.0f} soat "
        f"({left_h/24:.1f} kun)")
    add(f"G'oliblar soni: {winners} ta (budjet {fmt_votes(budget or 0)} so'm)")
    if far_at:
        add(f"Taqqoslash: {far_at:%d.%m %H:%M} va "
            f"{near_at:%d.%m %H:%M}" if near_at else "")
    else:
        add("Taqqoslash: tarix hali yetarli emas — keyingi ishga tushishlarda bo'ladi")
    add("")

    # ---------------- bizning loyihalar ----------------
    add("BIZNING LOYIHALAR")
    for label, pid in (("Asosiy  ", CONFIG.project_id),
                       ("Ikkinchi", CONFIG.second_project_id)):
        found = find_project(items, pid)
        if not found:
            add(f"  {label}: topilmadi ({pid})")
            continue
        rank, it = found
        f, n = far.get(pid), near.get(pid)
        add(f"  {label}: {rank}-o'rin, {fmt_votes(it.votes)} ovoz")
        if f:
            add(f"      {args.hours[0]:.0f} soat oldin : {fmt_votes(f.votes)} ovoz, "
                f"{f.rank}-o'rin")
        if n:
            add(f"      {args.hours[1]:.0f} soat oldin  : {fmt_votes(n.votes)} ovoz, "
                f"{n.rank}-o'rin")
        if f:
            hours = (now - f.at).total_seconds() / 3600
            r = (it.votes - f.votes) / hours if hours else 0
            add(f"      hozir           : +{it.votes - f.votes} ovoz "
                f"({r:.0f}/soat, {rate_label(r)})")
        add("")

    # ---------------- top ----------------
    add(f"TOP-{args.top} HARAKATI  (o'rin o'zgarishi: ^ ko'tarildi, v tushdi)")
    add(f"{'#':>3} {'Mahalla':<18} {'oldin':>7} {'hozir':>7} {'o‘sish':>7} "
        f"{'/soat':>6} {'o‘rin':>5}  holat")
    add("-" * 74)
    for i, it in enumerate(items[:args.top], 1):
        f = far.get(it.project_id)
        mark = ""
        if it.project_id == CONFIG.project_id:
            mark = " <<BIZ"
        elif it.project_id == CONFIG.second_project_id:
            mark = " <<BIZ2"
        if f:
            hours = (now - f.at).total_seconds() / 3600
            grew = it.votes - f.votes
            r = grew / hours if hours else 0
            add(f"{i:>3} {to_latin(it.quarter)[:18]:<18} {f.votes:>7} {it.votes:>7} "
                f"{grew:>+7} {r:>6.0f} {arrow(i, f.rank):>5}  {rate_label(r)}{mark}")
        else:
            add(f"{i:>3} {to_latin(it.quarter)[:18]:<18} {'—':>7} {it.votes:>7} "
                f"{'—':>7} {'—':>6} {'—':>5}  —{mark}")
        if i == winners:
            add(f"    {'—' * 20} BUDJET CHEGARASI {'—' * 20}")
    add("")

    # ---------------- xavflar ----------------
    found = find_project(items, CONFIG.project_id)
    if found and far:
        rank, our = found
        f_our = far.get(CONFIG.project_id)
        our_rate = ((our.votes - f_our.votes) /
                    ((now - f_our.at).total_seconds() / 3600)) if f_our else 0

        add("KIM BIZNI QUVIB O'TADI (hozirgi sur'atda)")
        threats = []
        for i, it in enumerate(items, 1):
            if it.votes >= our.votes or it.project_id == CONFIG.project_id:
                continue
            f = far.get(it.project_id)
            if not f:
                continue
            hours = (now - f.at).total_seconds() / 3600
            r = (it.votes - f.votes) / hours if hours else 0
            if r <= our_rate:
                continue
            t = (our.votes - it.votes) / (r - our_rate)
            if t <= left_h:
                threats.append((t, to_latin(it.quarter), our.votes - it.votes, r))
        if threats:
            for t, name, gap, r in sorted(threats)[:10]:
                add(f"  {t:>4.0f} soatdan keyin — {name:<18} "
                    f"({gap} ovoz orqada, {r:.0f}/soat)")
        else:
            add("  Yaqin muddatda hech kim yetib olmaydi")
        add("")

        # ---------------- maqsad ----------------
        add("QANCHA OVOZ KERAK (muddatgacha)")
        proj = []
        for it in items:
            f = far.get(it.project_id)
            if not f:
                proj.append(it.votes); continue
            hours = (now - f.at).total_seconds() / 3600
            r = (it.votes - f.votes) / hours if hours else 0
            proj.append(int(it.votes + r * left_h))
        proj.sort(reverse=True)
        for place in (3, 5, 10, winners or 19):
            if place <= len(proj):
                need = proj[place - 1] - our.votes
                per_h = need / left_h if left_h else 0
                if need > 0:
                    add(f"  {place:>2}-o'rin uchun: +{fmt_votes(need)} ovoz "
                        f"(soatiga {per_h:.0f})")
                else:
                    add(f"  {place:>2}-o'rin: hozirgi ovoz yetarli")

    text = "\n".join(out)
    print(text)

    if args.telegram:
        import httpx

        if not CONFIG.admin_chat_id:
            print("\n[ADMIN_CHAT_ID sozlanmagan — yuborilmadi]")
            return 0
        # Telegram cheki 4096 belgi; monospace blok ichida yuboriladi,
        # aks holda ustunlar tekislanmaydi.
        body = text[:3800].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        try:
            r = httpx.post(
                f"https://api.telegram.org/bot{CONFIG.bot_token}/sendMessage",
                data={"chat_id": CONFIG.admin_chat_id,
                      "text": f"<pre>{body}</pre>",
                      "parse_mode": "HTML"},
                timeout=30).json()
            print(f"\n[Telegram: {'yuborildi' if r.get('ok') else r}]")
        except Exception as exc:  # noqa: BLE001
            print(f"\n[Telegram xatosi: {exc}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
