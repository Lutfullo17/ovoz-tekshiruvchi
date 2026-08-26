"""
Groq orqali qisqa tahlil matni (rasm uchun caption).

Groq javob bermasa yoki xato qaytarsa — istisno tashlamaydi, `None` qaytaradi.
Chaqiruvchi tomon fallback matnni ishlatadi, rasm baribir yuboriladi.
"""
from __future__ import annotations

import json
import re
import logging

import httpx

from config import CONFIG

log = logging.getLogger(__name__)

API_URL = "https://api.groq.com/openai/v1/chat/completions"
MODELS_URL = "https://api.groq.com/openai/v1/models"

# Asosiy model ishlamasa shu tartibda sinaladi.
# Ro'yxat 26.08.2026 da Groq hisobida mavjud modellar bo'yicha tuzilgan.
# gpt-oss modellari "reasoning" ga token sarflab, content ni bo'sh qoldirishi
# mumkin — shuning uchun ular qwen'dan keyin turadi.
FALLBACK_MODELS = [
    "qwen/qwen3.8-27b",
    "qwen/qwen3.6-27b",
    "groq/compound-mini",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
]

SYSTEM_PROMPT = (
    "Sen ovoz berish monitoringi tahlilchisisan. Xabarni Telegram guruhdagi "
    "oddiy odamlar o'qiydi.\n"
    "\n"
    "Sen faqat KUZATUV yozasan. Tavsiya, maslahat yoki buyruq YOZMA.\n"
    "\n"
    "QAT'IY QOIDALAR:\n"
    "1. Faqat 2 ta jumla yoz. Har biri 12 so'zdan oshmasin.\n"
    "2. 1-jumla — 'top15_harakati' haqida: TOP-15 da kim eng ko'p ovoz "
    "olyapti va umumiy manzara qanday (ko'pchilik harakatdami yoki jim).\n"
    "3. 2-jumla — 'eng_yaqin_ortdagi' haqida: bizni quvayotgan mahalla "
    "yaqinlashyaptimi yoki ortda qolyaptimi.\n"
    "4. Mahalla nomini aniq ayt. Faqat berilgan raqamlardan foydalan.\n"
    "4a. Har bir jumlada FAQAT o'sha jumlaga tegishli maydondan foydalan. "
    "1-jumlada boshqa taqqoslash qo'shma. Ma'lumotda yo'q xulosani yozma "
    "('tenglashdik', 'quvib yetdik', 'yaqinlashdik' kabi) — farqni faqat "
    "'farq' maydonidan ol.\n"
    "5. Buyruq fe'llarini ISHLATMA: 'boshlang', 'yuboring', 'davom ettiring', "
    "'e'tibor qarating' kabi.\n"
    "6. Bo'sh gaplarni YOZMA: 'barqaror turibmiz', 'dinamika kuzatilmayapti', "
    "'holatni saqlash kerak'.\n"
    "7. Shartli taxmin qilma ('agar to'xtasa', 'agar davom etsa' kabi).\n"
    "8. Emoji ishlatma. O'zbek tilida (lotin alifbosida).\n"
    "\n"
    "IZOH: 'eng_yaqin_ortdagi' — reytingda bizdan keyingi mahalla (bizni quvayotgan). "
    "'top15_harakati' — TOP-15 ichida so'nggi yarim soatda ovoz olganlar, "
    "ko'p olganidan kam olganiga qarab tartiblangan; ro'yxatda yo'q mahallalar "
    "umuman ovoz olmagan. Bularni chalkashtirma. 'BIZ' — bizning mahalla.\n"
    "'oxirgi_30_daqiqa' — so'nggi yarim soatda qo'shilgan ovoz soni."
)

MAX_CAPTION = 1024


def _available_models(client: httpx.Client) -> list[str]:
    try:
        resp = client.get(MODELS_URL)
        resp.raise_for_status()
        return [m["id"] for m in resp.json().get("data", [])]
    except Exception as exc:  # noqa: BLE001
        log.warning("Groq modellar ro'yxatini olib bo'lmadi: %s", exc)
        return []


def _candidates(client: httpx.Client) -> list[str]:
    """Sinaladigan modellar: sozlamadagi + zaxira, mavjudlari bo'yicha filtrlangan."""
    wanted = [CONFIG.groq_model] + [m for m in FALLBACK_MODELS if m != CONFIG.groq_model]
    available = _available_models(client)
    if not available:
        return wanted
    ordered = [m for m in wanted if m in available]
    return ordered or available[:1]


def _is_reasoning_model(model: str) -> bool:
    """qwen3 va gpt-oss oilalari javobdan oldin "o'ylaydi"."""
    return model.startswith(("qwen/", "openai/gpt-oss"))


def _clean(text: str | None) -> str:
    """
    Reasoning modellari javobga <think>...</think> blokini qo'shadi.

    Ikki holat bor:
      * blok yopilgan  -> undan keyingi qismni olamiz
      * blok yopilmagan -> model max_tokens ga yetib, javob yozishga ulgurmagan.
        Bunda "" qaytariladi va chaqiruvchi keyingi modelga o'tadi.
    """
    if not text:
        return ""
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    elif "<think>" in text:
        return ""  # yopilmagan reasoning — javob yo'q
    return text.strip()


#: Modelning javobi katta guruhga chiqadi, shuning uchun oldindan tekshiriladi.
MAX_ANALYSIS = 400
_FORBIDDEN = re.compile(
    r"(https?://|www\.|t\.me/|@[A-Za-z0-9_]{3,}|<[a-zA-Z/]|\+998|\b\d{9,}\b)"
)


def is_safe(text: str) -> bool:
    """
    Model javobi guruhga yuborishga yaroqlimi?

    Xabar 485 kishilik guruhga ketadi, shuning uchun kutilmagan narsa
    (havola, telegram username, telefon raqami, HTML teg, juda uzun matn)
    bo'lsa umuman yuborilmaydi — faqat kodda hisoblangan faktlar qoladi.
    """
    if not text or len(text) > MAX_ANALYSIS:
        return False
    if _FORBIDDEN.search(text):
        return False
    if text.count("\n") > 6:
        return False
    letters = sum(ch.isalpha() for ch in text)
    return letters >= 20


def paragraphs(text: str) -> str:
    """
    Har bir jumlani alohida qatorga chiqaradi va orasiga bo'sh qator qo'yadi —
    Telegram'da o'qish oson bo'lsin.

    Model matnni bir uzun blok qilib yozadi, shuning uchun formatlash
    prompt'ga tayanmasdan, shu yerda deterministik tarzda bajariladi.
    """
    if not text:
        return ""
    # Nuqta/undov/so'roqdan keyin bosh harf bilan boshlangan joyda ajratamiz.
    # "3-o'rinda." kabi holatlar buzilmaydi, chunki keyingi belgi bo'sh joy bo'lishi shart.
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    return "\n\n".join(parts)


def build_payload(snapshot: dict) -> str:
    """Groq'ga yuboriladigan ma'lumotni ixcham JSON ga aylantiradi."""
    return json.dumps(snapshot, ensure_ascii=False, indent=None)


def analyze(snapshot: dict) -> str | None:
    """
    snapshot: {
        "biz": {"nom":..., "ovoz":..., "orin":..., "jami":..., "oxirgi_30_daqiqa":..., "oxirgi_24_soat":...},
        "orin_ozgardi": "5 -> 6" | None,
        "top": [{"orin":..,"nom":..,"ovoz":..,"oxirgi_30_daqiqa":..,"oxirgi_24_soat":..}, ...]
    }
    Qaytaradi: tahlil matni yoki None (xatoda).
    """
    if not CONFIG.groq_api_key:
        log.info("GROQ_API_KEY yo'q — tahlil o'tkazib yuborildi")
        return None

    headers = {
        "Authorization": f"Bearer {CONFIG.groq_api_key}",
        "Content-Type": "application/json",
    }
    body_base = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_payload(snapshot)},
        ],
        "max_tokens": 200,
        "temperature": 0.3,
    }

    with httpx.Client(headers=headers, timeout=CONFIG.request_timeout) as client:
        for model in _candidates(client):
            try:
                body = {**body_base, "model": model}
                # Reasoning modellari "o'ylash" ga token sarflab, javobsiz qolishi
                # mumkin. Groq buni o'chirishga ruxsat beradi.
                if _is_reasoning_model(model):
                    body["reasoning_effort"] = "none"

                resp = client.post(API_URL, json=body)
                if resp.status_code == 400 and "reasoning" in resp.text.lower():
                    # Model bu parametrni qo'llamas ekan — usiz qayta yuboramiz
                    body.pop("reasoning_effort", None)
                    resp = client.post(API_URL, json=body)
                if resp.status_code == 404 or (
                    resp.status_code == 400 and "model" in resp.text.lower()
                ):
                    log.warning("Groq modeli mavjud emas: %s", model)
                    continue
                resp.raise_for_status()
                text = _clean(resp.json()["choices"][0]["message"].get("content"))
                if text and is_safe(text):
                    log.info("Groq tahlili tayyor (%s)", model)
                    return text
                if text:
                    log.warning(
                        "Groq javobi tekshiruvdan o'tmadi (%s), tashlab yuborildi: %.80s",
                        model, text.replace("\n", " "),
                    )
                    return None  # shubhali matn — faktlar bilan cheklanamiz
                log.warning("Groq bo'sh javob qaytardi (%s) — keyingi model", model)
            except Exception as exc:  # noqa: BLE001
                log.warning("Groq xatosi (%s): %s", model, exc)

    log.error("Groq tahlili olinmadi — fallback ishlatiladi")
    return None


def fallback_caption(snapshot: dict) -> str:
    """Groq ishlamaganda — oddiy statistik qator."""
    biz = snapshot["biz"]
    parts = [f"{biz['nom']}: {biz['ovoz']} ovoz, {biz['orin']}/{biz['jami']}-o'rin"]

    d30 = biz.get("oxirgi_30_daqiqa")
    if d30 is not None:
        parts.append(f"oxirgi 30 daqiqada {d30:+d}")

    top = snapshot.get("top") or []
    ahead = [r for r in top if r["orin"] < biz["orin"]]
    behind = [r for r in top if r["orin"] > biz["orin"]]
    if ahead:
        nearest = ahead[-1]
        parts.append(f"oldingi: {nearest['nom']} ({nearest['ovoz'] - biz['ovoz']:+d})")
    if behind:
        chaser = behind[0]
        parts.append(f"ta'qibchi: {chaser['nom']} ({biz['ovoz'] - chaser['ovoz']:+d})")

    return " · ".join(parts)[:MAX_CAPTION]
