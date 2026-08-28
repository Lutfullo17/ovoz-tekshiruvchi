"""Chekka holatlarni tekshirish (tashqi tarmoqsiz, sintetik ma'lumot bilan)."""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from config import CONFIG, TASHKENT
from renderer import Row, build_rows, disambiguate, fmt_diff, fmt_votes, render
from scraper import Initiative, find_project
from storage import Storage

OK, FAIL = "  OK  ", " FAIL "
results: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    results.append((cond, label))
    print(f"[{OK if cond else FAIL}] {label}")


def make(pid: str, quarter: str, votes: int) -> Initiative:
    return Initiative(pid, quarter, votes, 1_647_855_000, "Ургут тумани", "test")


# ---------------------------------------------------------------- formatlash
check(fmt_votes(7441) == "7 441", "fmt_votes: 7441 -> '7 441'")
check(fmt_votes(274) == "274", "fmt_votes: uch xonali probelsiz")
check(fmt_diff(-274) == "−274", "fmt_diff: manfiy U+2212 minus bilan")
check(fmt_diff(722) == "+722", "fmt_diff: musbat '+' bilan")
check(fmt_diff(None) == "BIZ", "fmt_diff: bizning qator 'BIZ'")
check("−" in fmt_diff(-1896) and "-" not in fmt_diff(-1896),
      "fmt_diff: oddiy defis ishlatilmagan")

# ------------------------------------------------------- takroriy nomlar
names = disambiguate(["Юқори Тегана", "Навбоғ", "Юқори Тегана", "Юқори Тегана"])
check(names == ["Юқори Тегана", "Навбоғ", "Юқори Тегана (2)", "Юқори Тегана (3)"],
      "disambiguate: takroriy mahalla nomlariga (2), (3) qo'shiladi")

# ---------------------------------------------------- loyihani ID bo'yicha topish
items = [make("A1", "Юқори Санчиқул", 7443), make("B2", "Навбоғ", 7373),
         make("C3", "Қуйи Тегана", 7169)]
check(find_project(items, "C3")[0] == 3, "find_project: ID bo'yicha to'g'ri o'rin")
check(find_project(items, "Қуйи Тегана") is None,
      "find_project: nom bo'yicha topmaydi (faqat ID)")
check(find_project(items, "YOQ") is None, "find_project: mavjud bo'lmagan ID -> None")

# ------------------------------------------------------------ qatorlar
rows = build_rows(items, "C3", top_n=15)
check(len(rows) == 3 and rows[2].is_us, "build_rows: bizning qator belgilangan")
check(rows[0].diff == 7169 - 7443 and rows[0].diff < 0,
      "build_rows: bizdan yuqoridagilar manfiy farq")
check(rows[1].diff == 7169 - 7373, "build_rows: farq bizning ovozga nisbatan")

# TOP-N dan tashqarida qolgan holat
many = [make(f"P{i}", f"Mahalla {i}", 9000 - i * 100) for i in range(20)]
many.append(make("US", "Қуйи Тегана", 100))
many.sort(key=lambda i: i.votes, reverse=True)
rows_out = build_rows(many, "US", top_n=15)
check(len(rows_out) == 16 and rows_out[-1].is_us and rows_out[-1].rank == 21,
      "build_rows: TOP-15 dan tashqaridagi loyiha oxiriga qo'shiladi")

# ------------------------------------------------------------- jim vaqt
check(CONFIG.is_quiet(1) and CONFIG.is_quiet(3) and CONFIG.is_quiet(5),
      "is_quiet: 01:00, 03:00, 05:00 jim")
check(not CONFIG.is_quiet(6) and not CONFIG.is_quiet(0) and not CONFIG.is_quiet(23),
      "is_quiet: 06:00, 00:00, 23:00 jim emas")


class _Wrap:
    """QUIET_START > QUIET_END (yarim tundan o'tuvchi) holat."""
    quiet_start, quiet_end = 22, 6
    is_quiet = CONFIG.__class__.is_quiet


check(_Wrap.is_quiet(_Wrap, 23) and _Wrap.is_quiet(_Wrap, 2)
      and not _Wrap.is_quiet(_Wrap, 12),
      "is_quiet: yarim tundan o'tuvchi oraliq (22:00-06:00)")

# --------------------------------------------------------- baza va delta
with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "t.db"
    store = Storage(db)
    now = datetime.now(TASHKENT)

    a = [make("US", "Қуйи Тегана", 1000), make("R1", "Raqib", 900)]
    sid1 = store.save_snapshot(now - timedelta(hours=25), a)

    b = [make("US", "Қуйи Тегана", 1200), make("R1", "Raqib", 1150)]
    sid2 = store.save_snapshot(now - timedelta(minutes=30), b)

    c = [make("R1", "Raqib", 1400), make("US", "Қуйи Тегана", 1250)]
    sid3 = store.save_snapshot(now, c)

    d = store.deltas(sid3, now, {"US": 1250, "R1": 1400})
    check(d["US"].d30 == 50, "delta 30 daq: 1200 -> 1250 = +50")
    check(d["R1"].d30 == 250, "delta 30 daq: raqib +250")
    check(d["US"].d24h == 250, "delta 24 soat: 1000 -> 1250 = +250")

    check(store.last_rank("US", sid3) == 1, "last_rank: oldingi o'rin 1 edi")
    check(store.save_snapshot(now, c) > sid3, "save_snapshot: yangi id qaytaradi")

    first = Storage(Path(tmp) / "empty.db")
    empty = first.deltas(1, now, {"US": 100})
    check(empty["US"].d30 is None and empty["US"].d24h is None,
          "birinchi ishga tushish: delta None (jadvalda '—')")

    store.set_state("k", "v")
    check(store.get_state("k") == "v" and store.get_state("yoq", "d") == "d",
          "state: yozish/o'qish/default")

# ------------------------------------------------------------- rasm
demo = [Row(1, "Юқори Санчиқул", 7443, -274), Row(2, "Навбоғ", 7373, -204),
        Row(3, "Қуйи Тегана", 7169, None, is_us=True)]
png = render(demo, "26.08.2026, 17:10", "Urgut tumani")
check(png[:8] == b"\x89PNG\r\n\x1a\n", "render: haqiqiy PNG qaytaradi")
check(len(png) > 5000, "render: rasm bo'sh emas")

long_name = [Row(1, "Ж" * 60, 7443, -274)]
check(render(long_name, "26.08.2026", "Urgut tumani")[:4] == b"\x89PNG",
      "render: juda uzun nom rasmni buzmaydi")

# --------------------------------------------------------- caption uzunligi
from ai import MAX_CAPTION, fallback_caption

payload = {
    "biz": {"nom": "Қуйи Тегана", "ovoz": 7169, "orin": 3, "jami": 268,
            "oxirgi_30_daqiqa": 18, "oxirgi_24_soat": 420},
    "orin_ozgardi": None,
    "top": [{"orin": 1, "nom": "Юқори Санчиқул", "ovoz": 7443, "oxirgi_30_daqiqa": 5, "oxirgi_24_soat": 80},
            {"orin": 3, "nom": "Қуйи Тегана", "ovoz": 7169, "oxirgi_30_daqiqa": 18, "oxirgi_24_soat": 420},
            {"orin": 4, "nom": "Украч", "ovoz": 6447, "oxirgi_30_daqiqa": 1, "oxirgi_24_soat": 20}],
}
fb = fallback_caption(payload)
check(0 < len(fb) <= MAX_CAPTION, "fallback_caption: bo'sh emas va 1024 dan kam")
check("Қуйи Тегана" in fb and "3/268" in fb, "fallback_caption: asosiy raqamlar bor")

payload_empty = {"biz": {"nom": "X", "ovoz": 1, "orin": 1, "jami": 1,
                         "oxirgi_30_daqiqa": None, "oxirgi_24_soat": None},
                 "orin_ozgardi": None, "top": []}
check(len(fallback_caption(payload_empty)) > 0,
      "fallback_caption: bo'sh top bilan ham ishlaydi")

# -------------------------------------------------------- faktlar bloki
import bot
from storage import Delta

_items = [make(f"P{i}", f"Mahalla {i}", 9000 - i * 10) for i in range(20)]
_items[2] = make(CONFIG.project_id, "Қуйи Тегана", 8980)
_d = {it.project_id: Delta(d30=99 - i, d24h=500) for i, it in enumerate(_items)}
_fb = bot.facts_block(_items, 3, _items[2], _d)

check("3-o'rin" in _fb, "facts_block: o'rin ko'rsatilgan")
check("BIZDAN OLDINDA" in _fb and "BIZDAN ORTDA" in _fb,
      "facts_block: oldingi va ortdagi qatorlar bor")
check("BIZ" in _fb, "facts_block: bizning qator harakat ro'yxatida")
check(len(_fb) < 600, f"facts_block: ixcham ({len(_fb)} belgi)")

# Biz harakat ro'yxatining boshida bo'lmasak ham ko'rinishimiz kerak
_d2 = {it.project_id: Delta(d30=99 - i, d24h=5) for i, it in enumerate(_items)}
_d2[CONFIG.project_id] = Delta(d30=1, d24h=5)   # eng kam o'sish
_fb2 = bot.facts_block(_items, 3, _items[2], _d2)
check("BIZ +1" in _fb2, "facts_block: kam o'sganda ham 'BIZ' ro'yxatga qo'shiladi")

# Hech kim harakat qilmagan holat
_d3 = {it.project_id: Delta(d30=0, d24h=0) for it in _items}
_fb3 = bot.facts_block(_items, 3, _items[2], _d3)
check("🔥" not in _fb3, "facts_block: o'zgarish yo'q -> harakat qatori chiqmaydi")

# Birinchi ishga tushish (delta yo'q)
_d4 = {it.project_id: Delta(d30=None, d24h=None) for it in _items}
_fb4 = bot.facts_block(_items, 3, _items[2], _d4)
check("📍" in _fb4 and "🔥" not in _fb4,
      "facts_block: delta bo'lmasa asosiy qatorlar baribir chiqadi")

# 1-o'rinda turgan holat (oldinda hech kim yo'q)
_fb5 = bot.facts_block(_items, 1, _items[0], _d)
check("BIZDAN OLDINDA" not in _fb5 and "BIZDAN ORTDA" in _fb5,
      "facts_block: 1-o'rinda 'Oldinda' qatori chiqmaydi")

# Yangilanish holati qatori
check("yangi ovoz bo'lmadi" in bot.freshness_line(Delta(0, 0), _d3, datetime.now(TASHKENT)),
      "freshness_line: o'zgarish bo'lmasa aniq aytiladi")
check("birinchi tekshiruv" in bot.freshness_line(None, _d4, datetime.now(TASHKENT)),
      "freshness_line: birinchi ishga tushish belgilanadi")

# ------------------------------------------------- guruh xavfsizligi
# Xabar katta guruhga ketadi — kutilmagan narsa chiqmasligi kerak.
from ai import is_safe
from renderer import sanitize

check(is_safe("Xalqobod 58 ovoz bilan yetakchi. Ukrach ortda qolmoqda."),
      "is_safe: oddiy tahlil o'tadi")
check(not is_safe("Batafsil https://spam.example.com da"), "is_safe: havola bloklanadi")
check(not is_safe("Yozing @spamchi ga"), "is_safe: telegram username bloklanadi")
check(not is_safe("<b>qalin</b> matn bo'lsin bu yerda"), "is_safe: HTML teg bloklanadi")
check(not is_safe("Qo'ng'iroq qiling +998901234567"), "is_safe: telefon raqami bloklanadi")
check(not is_safe("A" * 500), "is_safe: juda uzun matn bloklanadi")
check(not is_safe("qisqa"), "is_safe: mazmunsiz kalta matn bloklanadi")
check(not is_safe(""), "is_safe: bo'sh matn bloklanadi")

check(sanitize("Mahalla https://spam.uz") == "Mahalla", "sanitize: havola olib tashlanadi")
check(sanitize("Mahalla @kanal") == "Mahalla", "sanitize: username olib tashlanadi")
check("<" not in sanitize("Mahalla <b>x</b>"), "sanitize: burchak qavslar olib tashlanadi")
check(len(sanitize("A" * 200)) <= 40, "sanitize: uzunlik cheklanadi")
check(sanitize("") == "—", "sanitize: bo'sh nom '—' bo'ladi")

check(hasattr(bot, "notify_admin") and not hasattr(bot, "send_message"),
      "texnik ogohlantirish faqat notify_admin orqali (guruhga yo'l yo'q)")
check("admin_chat_id" in bot.notify_admin.__code__.co_names,
      "notify_admin ADMIN_CHAT_ID ga yuboradi, CHAT_ID ga emas")
check("chat_id" in bot.send_photo.__code__.co_names,
      "send_photo hisobotni CHAT_ID ga yuboradi")

# ------------------------------------------- ikkinchi loyiha hisoboti
from renderer import build_rows_around

_ar = [make(f"R{i}", f"Mahalla {i}", 5000 - i * 100) for i in range(30)]
_ar[20] = make("FOCUS", "Қуйи Тегана", 2900)
_ar.sort(key=lambda x: x.votes, reverse=True)
_focus_rank = next(i for i, x in enumerate(_ar, 1) if x.project_id == "FOCUS")

_around = build_rows_around(_ar, "FOCUS", winners=19)
check(any(r.is_us for r in _around), "build_rows_around: markazdagi loyiha belgilangan")
check(_around[0].rank < _focus_rank < _around[-1].rank,
      "build_rows_around: yuqorisi va pastidagi qatorlar bor")
check(any(r.cutoff_after for r in _around) or _focus_rank > 23,
      "build_rows_around: chegara chizig'i oynaga tushsa ko'rsatiladi")
check(build_rows_around(_ar, "YOQ") == [],
      "build_rows_around: mavjud bo'lmagan ID -> bo'sh ro'yxat")
_no_w = build_rows_around(_ar, "FOCUS", winners=None)
check(not any(r.cutoff_after for r in _no_w),
      "build_rows_around: budjet noma'lum bo'lsa chegara chizilmaydi")

# Ikkinchi hisobot faqat o'z guruhiga ketishi kerak
import inspect
_src = inspect.getsource(bot.second_report)
check("CONFIG.second_chat_id" in _src and "CONFIG.chat_id" not in _src,
      "second_report faqat SECOND_CHAT_ID ga yuboradi")
check("CONFIG.second_chat_id" in _src and "return" in _src.split("chat =")[1][:200],
      "second_report: guruh sozlanmagan bo'lsa yubormaydi")

# ------------------------------------------------------------------ natija
print()
failed = [label for ok, label in results if not ok]
print(f"Jami: {len(results)} ta tekshiruv, {len(results) - len(failed)} ta o'tdi, "
      f"{len(failed)} ta yiqildi")
if failed:
    for label in failed:
        print("  YIQILDI:", label)
raise SystemExit(1 if failed else 0)
