"""
Sayt ishdan chiqqanda ishlatiladi: tiklanishini kutadi va tiklanishi bilan
joriy holatni ADMIN ga yuboradi.

    python watch-recovery.py [--minutes 180]

Guruhga yozmaydi — hisobot guruhga o'z vaqtida, botning odatdagi jadvali
bo'yicha boradi (jim vaqtda esa umuman bormaydi).
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

import httpx

from config import CONFIG, TASHKENT
from renderer import to_latin

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

CHECK_URL = (
    f"{CONFIG.api_base}/api/v2/info/board/{CONFIG.board_id}"
    f"?stage={CONFIG.stage}&page=0&size=5"
    f"&regionId={CONFIG.region_id}&districtId={CONFIG.district_id}"
)


def alive() -> bool:
    try:
        r = httpx.get(CHECK_URL, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        return r.status_code == 200 and bool(r.json().get("content"))
    except Exception:
        return False


def tell(text: str) -> None:
    if not CONFIG.admin_chat_id:
        print("ADMIN_CHAT_ID yo'q — yuborilmadi")
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{CONFIG.bot_token}/sendMessage",
            data={"chat_id": CONFIG.admin_chat_id, "text": text},
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        print("Yuborilmadi:", exc)


def report(down_since: datetime) -> str:
    from scraper import fetch_budget, fetch_sorted, find_project, winners_count

    items = fetch_sorted()
    rank, our = find_project(items, CONFIG.project_id)
    winners = winners_count(items, fetch_budget())
    now = datetime.now(TASHKENT)
    minutes = int((now - down_since).total_seconds() // 60)

    lines = [
        "✅ Sayt tiklandi",
        f"Ishlamagan vaqti: ~{minutes} daqiqa",
        "",
        f"📍 Quyi Tegana: {rank}-o'rin, {our.votes} ovoz",
    ]
    if winners:
        edge = items[winners - 1]
        lines.append(
            f"G'oliblik chegarasi: {winners}-o'rin ({edge.votes} ovoz)\n"
            f"Chegaradan {our.votes - edge.votes} ovoz yuqoridamiz"
        )
    lines += ["", "Eng yaqin raqiblar:"]
    for i, it in enumerate(items[:5], 1):
        mark = "  <- BIZ" if it.project_id == CONFIG.project_id else ""
        lines.append(f"{i}. {to_latin(it.quarter)} — {it.votes}{mark}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=180, help="qancha vaqt kutilsin")
    ap.add_argument("--every", type=int, default=90, help="necha soniyada tekshirilsin")
    args = ap.parse_args()

    start = datetime.now(TASHKENT)
    deadline = time.time() + args.minutes * 60
    print(f"Kuzatuv boshlandi {start:%H:%M} — har {args.every}s da tekshiriladi")

    checks = 0
    while time.time() < deadline:
        checks += 1
        if alive():
            print(f"[{datetime.now(TASHKENT):%H:%M:%S}] SAYT TIKLANDI ({checks}-urinish)")
            try:
                tell(report(start))
                print("Hisobot adminga yuborildi")
            except Exception as exc:  # noqa: BLE001
                tell(f"✅ Sayt tiklandi, lekin hisobot tayyorlanmadi: {exc}")
            return 0
        print(f"[{datetime.now(TASHKENT):%H:%M:%S}] hali javob yo'q ({checks})")
        time.sleep(args.every)

    print("Vaqt tugadi — sayt tiklanmadi")
    tell(f"⚠️ Sayt {args.minutes} daqiqadan beri javob bermayapti "
         f"({start:%H:%M} dan buyon).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
