"""
OpenBudget monitoring boti.

Har `INTERVAL_MINUTES` daqiqada:
  1. new.openbudget.uz dan tuman bo'yicha barcha tashabbuslarni oladi
  2. ovoz bo'yicha saralab TOP-N ni ajratadi
  3. snapshot'ni SQLite ga yozadi (delta hisoblash uchun)
  4. jadvalni PNG rasm qilib chizadi
  5. Groq'dan qisqa tahlil oladi va Telegram guruhga sendPhoto qiladi

Rejimlar:
    python bot.py                 # doimiy ishlash (scheduler)
    python bot.py --once          # bir marta to'liq sikl (Telegramga yuboradi)
    python bot.py --once --dry-run # bir marta, yubormasdan, terminalga chiqaradi
    python bot.py --render-only   # faqat rasm chizib faylga saqlaydi
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx

import ai
from config import CONFIG, TASHKENT
from renderer import build_rows, build_rows_around, fmt_votes, render, to_latin
from scraper import ScrapeError, fetch_budget, fetch_sorted, find_project, winners_count
from storage import Storage

log = logging.getLogger("bot")

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
MAX_CAPTION = 1024
STATE_FAILURES = "consecutive_failures"
STATE_ALERTED = "failure_alert_sent"
STATE_WAS_QUIET = "was_quiet"

# Caption'da nechta "harakatdagi" mahalla nomi ko'rsatiladi (qolgani "+N ta yana")
MOVERS_SHOWN = 6


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

def setup_logging() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    file_handler = logging.FileHandler(CONFIG.log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------

def send_photo(png: bytes, caption: str, chat_id: str | None = None) -> bool:
    chat_id = chat_id or CONFIG.chat_id
    if not CONFIG.bot_token or not chat_id:
        log.error("BOT_TOKEN yoki chat sozlanmagan — yuborilmadi")
        return False
    url = TELEGRAM_API.format(token=CONFIG.bot_token, method="sendPhoto")
    try:
        resp = httpx.post(
            url,
            data={"chat_id": chat_id, "caption": caption[:MAX_CAPTION]},
            files={"photo": ("reyting.png", png, "image/png")},
            timeout=CONFIG.request_timeout,
        )
        if resp.status_code != 200:
            # Telegram sababni javob tanasida yozadi — log'da ko'rinsin
            log.error("Telegram javobi: %s", resp.text[:300])
        resp.raise_for_status()
        log.info("Rasm yuborildi -> %s (%d bayt, caption %d belgi)",
                 chat_id, len(png), len(caption))
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("Telegramga yuborishda xato: %s", exc)
        return False


def notify_admin(text: str) -> bool:
    """
    Texnik ogohlantirish — FAQAT adminga (ADMIN_CHAT_ID), guruhga emas.

    Hisobot guruhi katta bo'lishi mumkin; u yerga xato xabarlari, loglar
    yoki diagnostika chiqmasligi kerak. ADMIN_CHAT_ID sozlanmagan bo'lsa
    ogohlantirish umuman yuborilmaydi, faqat logga yoziladi.
    """
    if not CONFIG.bot_token or not CONFIG.admin_chat_id:
        log.warning("ADMIN_CHAT_ID sozlanmagan — ogohlantirish yuborilmadi: %s", text)
        return False
    url = TELEGRAM_API.format(token=CONFIG.bot_token, method="sendMessage")
    try:
        resp = httpx.post(
            url,
            data={"chat_id": CONFIG.admin_chat_id, "text": text[:4096]},
            timeout=CONFIG.request_timeout,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("Telegram xabar xatosi: %s", exc)
        return False


# --------------------------------------------------------------------------
# Kechasi hisoboti
# --------------------------------------------------------------------------

def _neighbour(items, rank: int, deltas: dict, offset: int, our) -> dict | None:
    """Reytingda bizdan bitta oldin (-1) yoki keyin (+1) turgan loyiha."""
    index = rank - 1 + offset
    if not 0 <= index < len(items):
        return None
    it = items[index]
    d = deltas.get(it.project_id)
    return {
        "nom": to_latin(it.quarter),
        "orin": index + 1,
        "ovoz": it.votes,
        "farq": abs(it.votes - our.votes),
        "oxirgi_30_daqiqa": d.d30 if d else None,
    }


def facts_block(items, rank: int, our, deltas: dict, span: str = "oxirgi yarim soatda") -> str:
    """
    Caption'ning asosiy qismi — kodda hisoblangan aniq raqamlar.

    Model umumiy gap yozib qo'ysa ham ("barqaror turibmiz", "e'tibor qaratish
    kerak"), guruh baribir kerakli faktni ko'radi: o'rin, ovoz, kim yaqin,
    kim harakat qilyapti.
    """
    i = rank - 1
    # Har bir blok: sarlavha + ostida ma'lumot. Bloklar bo'sh qator bilan ajraladi.
    blocks = [f"📍 BIZ: {rank}-o'rin · {fmt_votes(our.votes)} ovoz"]

    # Oldindagi ikkitasi — farq bilan
    ahead = items[max(0, i - 2):i]
    if ahead:
        parts = [f"{to_latin(x.quarter)} — {fmt_votes(x.votes - our.votes)} ovoz ko'p"
                 for x in reversed(ahead)]
        blocks.append("⬆️ BIZDAN OLDINDA\n" + "\n".join(parts))

    # Ortdagi ikkitasi — kim yaqinlashyapti
    behind = items[i + 1:i + 3]
    if behind:
        parts = [f"{to_latin(x.quarter)} — {fmt_votes(our.votes - x.votes)} ovoz kam"
                 for x in behind]
        blocks.append("⬇️ BIZDAN ORTDA\n" + "\n".join(parts))

    # TOP-N ichida oxirgi 30 daqiqada kim qancha ovoz oldi
    top = list(enumerate(items[: CONFIG.top_n], start=1))
    moves = [
        (pos, it, deltas[it.project_id].d30)
        for pos, it in top
        if deltas.get(it.project_id) and deltas[it.project_id].d30
    ]
    active = sorted((m for m in moves if m[2] > 0), key=lambda x: x[2], reverse=True)

    if active:
        shown = active[:MOVERS_SHOWN]
        # Bizning qator ro'yxatga tushmay qolsa ham doim ko'rsatiladi —
        # guruh o'zini boshqalar bilan solishtira olishi kerak.
        ours = next((m for m in active if m[1].project_id == CONFIG.project_id), None)
        if ours and ours not in shown:
            shown = shown[:MOVERS_SHOWN - 1] + [ours]

        parts = []
        for pos, it, d in shown:
            label = "BIZ" if it.project_id == CONFIG.project_id else to_latin(it.quarter)
            parts.append(f"{pos}. {label} +{d}")
        tail = len(active) - len(shown)
        if tail:
            parts.append(f"...yana {tail} ta")

        block = "🔥 OXIRGI YARIM SOATDA OLINGAN OVOZ\n" + "\n".join(parts)
        asleep = CONFIG.top_n - len(active)
        if asleep > 0:
            block += f"\nQolgan {asleep} ta mahalla ovoz olmadi"
        blocks.append(block)
    elif moves:
        blocks.append(f"😴 TOP-{CONFIG.top_n} da yangi ovoz yo'q")

    return "\n\n".join(blocks)


def _elapsed_phrase(since: datetime | None, now: datetime) -> str:
    """"oxirgi yarim soatda" / "oxirgi 3 soatda" — haqiqiy oraliqqa qarab."""
    if since is None:
        return "oxirgi tekshiruvdan beri"
    minutes = max(1, int((now - since).total_seconds() // 60))
    if minutes < 45:
        return "oxirgi yarim soatda"
    hours = round(minutes / 60)
    if hours < 24:
        return f"oxirgi {hours} soatda"
    return f"oxirgi {round(hours / 24)} kunda"


def second_report(items, deltas, winners, span, now) -> None:
    """
    Ikkinchi loyihaga qaratilgan alohida hisobot — o'z guruhiga.

    Asosiy hisobotdan farqi: jadval reytingning yuqorisini emas, shu
    loyiha atrofidagi qatorlarni ko'rsatadi — kim oldinda, kim ortda.
    """
    if not CONFIG.second_chat_id:
        return
    pid = CONFIG.second_project_id
    found = find_project(items, pid)
    if not found:
        log.warning("Ikkinchi loyiha topilmadi, hisobot yuborilmadi: %s", pid)
        return

    rank, it = found
    rows = build_rows_around(items, pid, winners=winners)
    if not rows:
        return

    png = render(rows, now.strftime('%d.%m.%Y, %H:%M'), CONFIG.district_label)

    d = deltas.get(pid)
    gained = d.d30 if d and d.d30 else 0
    blocks = [f"📍 BIZNING LOYIHA: {rank}-o'rin · {fmt_votes(it.votes)} ovoz"]

    ahead = items[max(0, rank - 3):rank - 1]
    if ahead:
        lines = [f"{to_latin(x.quarter)} — {fmt_votes(x.votes - it.votes)} ovoz ko'p"
                 for x in reversed(ahead)]
        blocks.append('⬆️ BIZDAN OLDINDA\n' + '\n'.join(lines))

    behind = items[rank:rank + 2]
    if behind:
        lines = [f"{to_latin(x.quarter)} — {fmt_votes(it.votes - x.votes)} ovoz kam"
                 for x in behind]
        blocks.append('⬇️ BIZDAN ORTDA\n' + '\n'.join(lines))

    if winners and 0 < winners <= len(items):
        edge = items[winners - 1]
        if rank <= winners:
            blocks.append(
                f"✅ G'OLIBLAR ICHIDAMIZ\n"
                f"Chegara — {winners}-o'rin. Pastdagidan "
                f"{fmt_votes(it.votes - edge.votes)} ovoz oldinamiz.")
        else:
            need = edge.votes - it.votes + 1
            blocks.append(
                f"🎯 G'OLIBLIKKACHA\n"
                f"Yana {fmt_votes(need)} ovoz kerak "
                f"({winners}-o'ringa chiqish uchun).")

    if gained > 0:
        blocks.append(f"{span.capitalize()} {fmt_votes(gained)} ta yangi ovoz oldik.")
    else:
        blocks.append(f"{span.capitalize()} yangi ovoz qo'shilmadi.")

    blocks.append(f"🕘 {now.strftime('%H:%M')} dagi holat")
    send_photo(png, '\n\n'.join(blocks), chat_id=CONFIG.second_chat_id)


def second_project_line(items, deltas: dict, span: str,
                        winners: int | None = None) -> str | None:
    """
    Mahallaning ikkinchi loyihasi haqida qisqa xabar — gap ko'rinishida.

    Sarlavha o'sish sur'atiga qarab o'zgaradi: qimirlamagan loyiha bilan
    chegaraga yaqinlashib qolgan loyiha bir xil o'qilmasligi kerak.
    """
    pid = CONFIG.second_project_id
    if not pid or pid.lower() in ("none", "off", "yo'q"):
        return None
    found = find_project(items, pid)
    if not found:
        log.warning("Ikkinchi loyiha topilmadi: %s", pid)
        return None

    rank, it = found
    d = deltas.get(pid)
    gained = d.d30 if d and d.d30 else 0

    if gained >= 200:
        head = "🚀 IKKINCHI LOYIHAMIZ SHIDDAT BILAN KETMOQDA"
    elif gained >= 50:
        head = "📈 IKKINCHI LOYIHAMIZ TEZ KO'TARILMOQDA"
    elif gained > 0:
        head = "🌱 IKKINCHI LOYIHAMIZ ASTA O'SMOQDA"
    else:
        head = "😴 IKKINCHI LOYIHAMIZ QIMIRLAMADI"

    sentences = [f"Hozir {rank}-o'rinda, {fmt_votes(it.votes)} ovoz to'plagan."]
    if gained > 0:
        sentences.append(f"{span.capitalize()} {fmt_votes(gained)} ta yangi ovoz oldi.")

    if winners and 0 < winners <= len(items):
        edge = items[winners - 1]
        if rank <= winners:
            sentences.append("G'oliblar ro'yxatiga kirdi!")
        else:
            need = edge.votes - it.votes + 1
            sentences.append(
                f"G'oliblik chegarasigacha yana {fmt_votes(need)} ovoz kerak."
            )

    return head + "\n" + "\n".join(sentences)


def freshness_line(our_delta, deltas: dict, now: datetime,
                   since: datetime | None = None) -> str:
    """
    Oxirgi qator: ma'lumot qachon olingani va nima o'zgargani.

    Sayt ovozlarni real vaqtda emas, to'p-to'p yangilaydi — ba'zan 30 daqiqada
    umuman o'zgarish bo'lmaydi. Bu qatorsiz "sayt turibdi" bilan "bot qotib
    qolgan" holatini farqlab bo'lmaydi.
    """
    stamp = now.strftime("%H:%M")
    head = f"🕘 {stamp} dagi holat"
    span = _elapsed_phrase(since, now)

    if our_delta is None or our_delta.d30 is None:
        return f"{head}\nBu birinchi tekshiruv — taqqoslash uchun oldingi ma'lumot yo'q"

    district = sum(d.d30 for d in deltas.values() if d.d30 is not None)
    if district == 0:
        return f"{head}\n{span.capitalize()} butun tumanda birorta yangi ovoz bo'lmadi"

    ours = our_delta.d30
    if ours <= 0:
        return (f"{head}\n{span.capitalize()} bizga yangi ovoz qo'shilmadi "
                f"(butun tumanga {district} ta qo'shildi)")
    return (f"{head}\n{span.capitalize()} bizga {ours} ta yangi ovoz qo'shildi "
            f"(butun tumanga {district} ta)")


def night_summary(store: Storage, now: datetime, our_id: str) -> str | None:
    """06:00 dan keyingi birinchi xabar uchun: jim vaqtdagi o'sish."""
    end = now.replace(hour=CONFIG.quiet_end, minute=0, second=0, microsecond=0)
    if end > now:
        end -= timedelta(days=1)
    start = end.replace(hour=CONFIG.quiet_start, minute=0, second=0, microsecond=0)
    if start >= end:
        start -= timedelta(days=1)

    before, after = store.votes_between(start, end)
    if not before or not after or our_id not in before or our_id not in after:
        return None

    our_growth = after[our_id] - before[our_id]
    # Kechasi eng ko'p o'sgan raqib (biz emas)
    rivals = [
        (pid, after[pid] - before[pid])
        for pid in after
        if pid != our_id and pid in before
    ]
    if not rivals:
        return f"Kechasi ({CONFIG.quiet_start:02d}:00–{CONFIG.quiet_end:02d}:00): biz {our_growth:+d}"

    top_pid, top_growth = max(rivals, key=lambda x: x[1])
    return (
        f"Kechasi ({CONFIG.quiet_start:02d}:00–{CONFIG.quiet_end:02d}:00): "
        f"biz {our_growth:+d}, eng faol raqib {top_growth:+d}"
    )


# --------------------------------------------------------------------------
# Asosiy sikl
# --------------------------------------------------------------------------

def run_cycle(send: bool = True, render_path: Path | None = None,
              analysis: bool = True) -> bool:
    store = Storage(CONFIG.db_path)
    now = datetime.now(TASHKENT)

    # ---- 1. Ma'lumot ----
    try:
        items = fetch_sorted()
    except ScrapeError as exc:
        failures = int(store.get_state(STATE_FAILURES, "0")) + 1
        store.set_state(STATE_FAILURES, str(failures))
        log.error("Ma'lumot olinmadi (ketma-ket %d-marta): %s", failures, exc)
        # Faqat 3-xatolikda bitta ogohlantirish — spam bo'lmasin
        if failures >= CONFIG.max_retries and store.get_state(STATE_ALERTED) != "1":
            if send:
                notify_admin(
                    f"⚠️ Monitoring: sayt {failures} marta ketma-ket javob bermadi. "
                    "Ma'lumot yangilanmayapti."
                )
            store.set_state(STATE_ALERTED, "1")
        return False

    if store.get_state(STATE_FAILURES, "0") != "0":
        log.info("Aloqa tiklandi")
    store.set_state(STATE_FAILURES, "0")
    store.set_state(STATE_ALERTED, "0")

    # ---- 2. Bizning loyiha (FAQAT ID bo'yicha) ----
    found = find_project(items, CONFIG.project_id)
    if not found:
        log.error(
            "PROJECT_ID=%s ro'yxatda topilmadi (%d ta yozuv tekshirildi). "
            ".env dagi ID ni tekshiring.",
            CONFIG.project_id, len(items),
        )
        return False
    rank, our = found

    # ---- 3. Snapshot va delta ----
    snapshot_id = store.save_snapshot(now, items)
    store.prune()  # baza cheksiz o'smasin (24 soatlik delta saqlanadi)
    current = {it.project_id: it.votes for it in items}
    deltas = store.deltas(snapshot_id, now, current)
    prev_rank = store.last_rank(CONFIG.project_id, snapshot_id)

    # ---- 4. Rasm ----
    # Budjet kampaniya davomida o'zgaradi (26.08.2026 da 24,72 -> 31,671 mlrd),
    # shuning uchun g'oliblar soni har safar qaytadan hisoblanadi.
    budget = fetch_budget()
    winners = winners_count(items, budget)
    if winners:
        log.info("Budjet %s so'm -> %d ta g'olib", f"{budget:,}".replace(",", " "), winners)
    rows = build_rows(items, CONFIG.project_id, CONFIG.top_n, winners=winners)
    timestamp = now.strftime("%d.%m.%Y, %H:%M")
    png = render(rows, timestamp, CONFIG.district_label)

    if render_path:
        render_path.write_bytes(png)
        log.info("Rasm saqlandi: %s (%d bayt, %d qator)",
                 render_path, len(png), len(rows))

    # --render-only: faqat dizaynni tekshirish — Groq va caption kerak emas
    if not analysis:
        return True

    # ---- 5. Tahlil uchun ma'lumot ----
    our_delta = deltas.get(CONFIG.project_id)
    payload = {
        "biz": {
            "nom": to_latin(our.quarter),
            "ovoz": our.votes,
            "orin": rank,
            "jami": len(items),
            "oxirgi_30_daqiqa": our_delta.d30 if our_delta else None,
            "oxirgi_24_soat": our_delta.d24h if our_delta else None,
        },
        "orin_ozgardi": (
            f"{prev_rank} -> {rank}" if prev_rank is not None and prev_rank != rank else None
        ),
        # Model "eng faol" bilan "eng yaqin" ni chalkashtirmasligi uchun
        # ikkalasi ham oldindan hisoblab beriladi.
        "eng_yaqin_ortdagi": _neighbour(items, rank, deltas, +1, our),
        "eng_yaqin_oldingi": _neighbour(items, rank, deltas, -1, our),
        "top15_harakati": sorted(
            (
                {
                    "orin": pos,
                    "nom": "BIZ" if it.project_id == CONFIG.project_id
                           else to_latin(it.quarter),
                    "oxirgi_30_daqiqa": deltas[it.project_id].d30,
                }
                for pos, it in enumerate(items[: CONFIG.top_n], start=1)
                if deltas.get(it.project_id) and deltas[it.project_id].d30
            ),
            key=lambda x: x["oxirgi_30_daqiqa"], reverse=True,
        ),
        "top": [
            {
                "orin": i + 1,
                "nom": to_latin(it.quarter),
                "ovoz": it.votes,
                "oxirgi_30_daqiqa": deltas[it.project_id].d30,
                "oxirgi_24_soat": deltas[it.project_id].d24h,
            }
            for i, it in enumerate(items[: CONFIG.top_n])
        ],
    }

    # ---- 6. Jim vaqt ----
    quiet = CONFIG.is_quiet(now.hour)
    if quiet:
        store.set_state(STATE_WAS_QUIET, "1")
        log.info("Jim vaqt (%02d:00) — ma'lumot saqlandi, xabar yuborilmadi", now.hour)
        return True

    # ---- 7. Caption ----
    lines: list[str] = []
    if store.get_state(STATE_WAS_QUIET) == "1":
        summary = night_summary(store, now, CONFIG.project_id)
        if summary:
            lines.append(f"🌙 {summary}")
        store.set_state(STATE_WAS_QUIET, "0")

    if prev_rank is not None and prev_rank != rank:
        arrow = "⬆️" if rank < prev_rank else "⬇️"
        lines.append(f"{arrow} O'RIN O'ZGARDI: {prev_rank} → {rank}")

    # Aniq raqamlar — kodda hisoblanadi, modelga bog'liq emas
    span = _elapsed_phrase(store.previous_snapshot_time(snapshot_id), now)
    lines.append(facts_block(items, rank, our, deltas, span))

    second = second_project_line(items, deltas, span, winners)
    if second:
        lines.append(second)

    # Groq'dan 2 ta qisqa jumla — faqat aytadigan gap bo'lsa.
    # Hech narsa o'zgarmagan bo'lsa model bo'sh jumla to'qiydi, shuning uchun
    # umuman chaqirilmaydi: faktlar bloki o'zi yetarli.
    district_delta = sum(d.d30 for d in deltas.values() if d.d30 is not None)
    first_run = our_delta is None or our_delta.d30 is None
    if first_run or district_delta != 0:
        analysis = ai.analyze(payload)
        if analysis:
            lines.append(ai.paragraphs(analysis))
    else:
        log.info("O'zgarish yo'q — Groq tahlili o'tkazib yuborildi")

    # Yangilanish holati — "sayt o'zgarmadi" bilan "bot qotib qoldi" ni ajratish uchun
    lines.append(freshness_line(our_delta, deltas, now,
                               store.previous_snapshot_time(snapshot_id)))

    caption = "\n\n".join(lines)
    if len(caption) > MAX_CAPTION:
        caption = caption[: MAX_CAPTION - 1].rstrip() + "…"

    # ---- 8. Yuborish ----
    if send:
        send_photo(png, caption)
        # Ikkinchi loyihaga qaratilgan hisobot — o'z guruhiga
        try:
            second_report(items, deltas, winners, span, now)
        except Exception as exc:  # noqa: BLE001 — asosiy hisobot buzilmasin
            log.error("Ikkinchi hisobot yuborilmadi: %s", exc)
    else:
        print("\n--- CAPTION ---")
        print(caption)
        print("--- /CAPTION ---\n")
        print(f"Rasm: {len(png)} bayt | {our.quarter}: {our.votes} ovoz, "
              f"{rank}/{len(items)}-o'rin")
    return True


# --------------------------------------------------------------------------
# Scheduler
# --------------------------------------------------------------------------

def run_forever() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler(timezone=TASHKENT)
    scheduler.add_job(
        run_cycle,
        "interval",
        minutes=CONFIG.interval_minutes,
        next_run_time=datetime.now(TASHKENT),
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    log.info(
        "Bot ishga tushdi | interval: %d daq | jim vaqt: %02d:00–%02d:00 | loyiha: %s",
        CONFIG.interval_minutes, CONFIG.quiet_start, CONFIG.quiet_end, CONFIG.project_id,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("To'xtatildi")


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenBudget monitoring boti")
    parser.add_argument("--once", action="store_true",
                        help="bir marta ishlab to'xtaydi")
    parser.add_argument("--dry-run", action="store_true",
                        help="--once bilan: Telegramga yubormaydi, terminalga chiqaradi")
    parser.add_argument("--render-only", action="store_true",
                        help="faqat rasm chizib faylga saqlaydi")
    parser.add_argument("-o", "--output", default="test_output.png",
                        help="--render-only uchun fayl nomi")
    args = parser.parse_args()

    setup_logging()

    if args.render_only:
        ok = run_cycle(send=False, render_path=Path(args.output), analysis=False)
        return 0 if ok else 1
    if args.once:
        ok = run_cycle(send=not args.dry_run)
        return 0 if ok else 1

    run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
