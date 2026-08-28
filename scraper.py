"""
Ochiq budjet (new.openbudget.uz) dan tashabbuslar ro'yxatini olish.

API QANDAY TOPILGAN
-------------------
Sahifa Nuxt (Vue) SPA. Brauzer DevTools > Network da sahifa yuklanganda
quyidagi ichki so'rovlar ketadi:

    GET /api/v1/regions
    GET /api/v1/districts?offset=<N>&limit=100
    GET /api/v1/boards/<boardId>
    GET /api/v2/info/board/<boardId>?stage=PASSED&page=0&size=12
    GET /api/v2/info/statistics/board-budget-sum/<boardId>?regionId=<id>

Ro'yxat uchun kerakligi — quyidagisi:

    GET /api/v2/info/board/55?stage=PASSED&page=N&size=50&regionId=8&districtId=93

    regionId=8    -> Samarqand viloyati
    districtId=93 -> Urgut tumani  (/api/v1/districts dan aniqlangan; u endpoint
                     offset/limit bilan sahifalanadi, page/size bilan emas)

Javob — Spring Data Page ko'rinishida:
    {"content": [...], "totalElements": 268, "totalPages": N, "last": bool, "size": 36}

MUHIM NUANS: `size` parametri so'ralganidan qat'i nazar serverda 36 ga
cheklanadi, shuning uchun `last=true` bo'lguncha aylanib chiqiladi.

Har bir yozuvdagi kerakli maydonlar:
    publicId, quarterName, voteCount, grantedAmount, districtName, description

API ovoz bo'yicha saralab bermaydi, shuning uchun barcha yozuvlar olinib,
mahalliy tarzda saralanadi.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from config import CONFIG

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class ScrapeError(RuntimeError):
    """Ma'lumot olib bo'lmadi (barcha usullar muvaffaqiyatsiz)."""


@dataclass(frozen=True)
class Initiative:
    project_id: str      # publicId
    quarter: str         # quarterName (MFY nomi, kirill)
    votes: int
    amount: int
    district: str
    description: str
    #: Sherikchilik asosida, ovoz berishdan oldin g'olib bo'lgan loyiha.
    #: Bunday loyihalar ovozda qatnashmaydi, lekin puli tuman budjetidan
    #: ushlanadi — ya'ni ovoz g'oliblariga qoladigan mablag'ni kamaytiradi.
    is_partnership: bool = False

    @classmethod
    def from_api(cls, item: dict) -> "Initiative":
        return cls(
            project_id=str(item.get("publicId") or "").strip(),
            quarter=(item.get("quarterName") or "—").strip(),
            votes=int(item.get("voteCount") or 0),
            amount=int(item.get("grantedAmount") or 0),
            district=(item.get("districtName") or "").strip(),
            description=(item.get("description") or "").strip(),
            is_partnership=bool(item.get("isPartnership")),
        )


def _api_path(page: int) -> str:
    return (
        f"/api/v2/info/board/{CONFIG.board_id}"
        f"?stage={CONFIG.stage}&page={page}&size=50"
        f"&regionId={CONFIG.region_id}&districtId={CONFIG.district_id}"
        f"&_={int(time.time() * 1000)}"
    )


# --------------------------------------------------------------------------
# 1-usul: to'g'ridan-to'g'ri JSON API
# --------------------------------------------------------------------------

def fetch_via_api() -> list[Initiative]:
    items: list[Initiative] = []
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Referer": CONFIG.page_url,
    }
    with httpx.Client(
        base_url=CONFIG.api_base,
        headers=headers,
        timeout=CONFIG.request_timeout,
        follow_redirects=True,
    ) as client:
        page = 0
        while page < 50:  # xavfsizlik cheki
            resp = client.get(_api_path(page))
            resp.raise_for_status()
            data = resp.json()
            content = data.get("content") or []
            items.extend(Initiative.from_api(it) for it in content)
            if data.get("last") or not content:
                break
            page += 1
    if not items:
        raise ScrapeError("API bo'sh ro'yxat qaytardi")
    return items


# --------------------------------------------------------------------------
# 2-usul: Playwright (headless chromium) — API to'g'ridan-to'g'ri ishlamasa
# --------------------------------------------------------------------------

_PW_SCRIPT = """
async (cfg) => {
    const out = [];
    for (let page = 0; page < 50; page++) {
        const url = `/api/v2/info/board/${cfg.board}?stage=${cfg.stage}`
                  + `&page=${page}&size=50&regionId=${cfg.region}`
                  + `&districtId=${cfg.district}&_=${Date.now()}`;
        const resp = await fetch(url, {cache: 'no-store'});
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const data = await resp.json();
        out.push(...(data.content || []));
        if (data.last || !(data.content || []).length) break;
    }
    return out;
}
"""


def fetch_via_playwright() -> list[Initiative]:
    """
    Haqiqiy brauzer ichida sahifani ochib, o'sha API ni sahifa kontekstidan
    chaqiradi. Bu Cloudflare/JS-gate holatlarida ham ishlaydi, chunki so'rov
    to'liq render bo'lgan sahifadan, uning cookie va header'lari bilan ketadi.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ScrapeError(
            "Playwright o'rnatilmagan. "
            "`pip install playwright && playwright install chromium`"
        ) from exc

    args = {
        "board": CONFIG.board_id,
        "stage": CONFIG.stage,
        "region": CONFIG.region_id,
        "district": CONFIG.district_id,
    }
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(
                CONFIG.page_url,
                wait_until="domcontentloaded",
                timeout=CONFIG.request_timeout * 1000,
            )
            raw = page.evaluate(_PW_SCRIPT, args)
        finally:
            browser.close()

    items = [Initiative.from_api(it) for it in raw]
    if not items:
        raise ScrapeError("Playwright bo'sh ro'yxat qaytardi")
    return items


# --------------------------------------------------------------------------
# Ommaviy interfeys
# --------------------------------------------------------------------------

def fetch_sorted(retries: int | None = None) -> list[Initiative]:
    """
    Barcha tashabbuslarni olib, ovoz bo'yicha kamayish tartibida qaytaradi.
    Retry: eksponensial backoff (2s, 4s, 8s).
    """
    retries = CONFIG.max_retries if retries is None else retries
    mode = CONFIG.scraper_mode

    methods = []
    if mode in ("auto", "api"):
        methods.append(("API", fetch_via_api))
    if mode in ("auto", "playwright"):
        methods.append(("Playwright", fetch_via_playwright))
    if not methods:
        raise ScrapeError(f"Noma'lum SCRAPER_MODE: {mode}")

    last_error: Exception | None = None
    for name, method in methods:
        for attempt in range(1, retries + 1):
            try:
                items = method()
                log.info("%s: %d ta yozuv olindi (urinish %d)", name, len(items), attempt)
                # Sahifalash chegarasida takror kelishi mumkin — ID bo'yicha yagonalashtiramiz
                unique = {it.project_id: it for it in items}
                return sorted(unique.values(), key=lambda i: i.votes, reverse=True)
            except Exception as exc:  # noqa: BLE001 — har qanday xatoda qayta urinamiz
                last_error = exc
                log.warning("%s urinish %d/%d muvaffaqiyatsiz: %s", name, attempt, retries, exc)
                if attempt < retries:
                    time.sleep(2 ** attempt)
        log.error("%s butunlay muvaffaqiyatsiz", name)

    raise ScrapeError(f"Ma'lumot olinmadi: {last_error}")


def fetch_budget() -> int | None:
    """
    Tumanga ajratilgan umumiy mablag'.

    Bu raqam kampaniya davomida o'zgarishi mumkin — 26.08.2026 da Urgut
    tumani uchun 24,72 mlrd dan 31,671 mlrd so'mga oshdi va g'oliblar soni
    15 tadan 19 taga ko'paydi. Shuning uchun qat'iy yozib qo'yilmaydi,
    har safar saytdan o'qiladi.

    Olib bo'lmasa None qaytaradi — chaqiruvchi chegarasiz davom etadi.
    """
    url = (f"/api/v2/info/statistics/board-budget-sum/{CONFIG.board_id}"
           f"?regionId={CONFIG.region_id}&districtId={CONFIG.district_id}"
           f"&_={int(time.time() * 1000)}")
    try:
        with httpx.Client(base_url=CONFIG.api_base, timeout=CONFIG.request_timeout,
                          headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                   "Referer": CONFIG.page_url},
                          follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            value = int(resp.json().get("budgetSum") or 0)
            return value or None
    except Exception as exc:  # noqa: BLE001
        log.warning("Budjet miqdori olinmadi: %s", exc)
        return None


def winners_count(items: list[Initiative], budget: int | None) -> int | None:
    """
    Budjetga nechta loyiha sig'adi (ovoz bo'yicha yuqoridan pastga).

    G'oliblar reyting bo'yicha, mablag' tugaguncha aniqlanadi.

    Sherikchilik loyihalari ovoz berishdan oldin g'olib bo'lgan, lekin
    ularning puli ham shu tuman budjetidan ketadi (tekshirildi: barcha
    viloyat budjetlari yig'indisi doskaning total_amount iga teng, ya'ni
    sherikchilik uchun alohida jamg'arma ajratilmagan). Shuning uchun
    ularning summasi budjetdan avval ayriladi.
    """
    if not budget:
        return None

    committed = sum(it.amount for it in items if it.is_partnership)
    available = budget - committed

    spent = 0
    for index, item in enumerate(items):
        if item.is_partnership:
            continue          # allaqachon g'olib, ovozda qatnashmaydi
        if spent + item.amount > available:
            return index
        spent += item.amount
    return len(items)


def find_project(items: list[Initiative], project_id: str) -> tuple[int, Initiative] | None:
    """Loyihani FAQAT ID bo'yicha topadi. Qaytaradi: (o'rin, yozuv) yoki None."""
    for index, item in enumerate(items):
        if item.project_id == project_id:
            return index + 1, item
    return None


if __name__ == "__main__":
    import sys

    # Windows konsoli sukut bo'yicha cp1252 — kirill harflar uchun UTF-8 majburlanadi
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    rows = fetch_sorted()
    found = find_project(rows, CONFIG.project_id)
    our_votes = found[1].votes if found else 0

    print(f"\nJami: {len(rows)} ta loyiha ({CONFIG.district_label})\n")
    print(f"{'#':>3}  {'Mahalla':<22} {'Ovoz':>7}  {'Farq':>8}")
    print("-" * 46)
    for i, r in enumerate(rows[: CONFIG.top_n], start=1):
        if found and r.project_id == CONFIG.project_id:
            diff = "BIZ"
        elif our_votes:
            diff = f"{our_votes - r.votes:+d}"
        else:
            diff = "—"
        print(f"{i:>3}  {r.quarter:<22} {r.votes:>7}  {diff:>8}")

    print()
    if found:
        rank, item = found
        print(f"Bizning loyiha [{CONFIG.project_id}]: {item.votes} ovoz, "
              f"{rank}/{len(rows)}-o'rin")
        print(f"  {item.description[:90]}")
    else:
        print(f"!!! DIQQAT: {CONFIG.project_id} ID li loyiha ro'yxatda topilmadi.")
