"""
Trend jadvalini rangli PNG qilib chizadi.

Oddiy reyting jadvalidan farqi: bu yerda har bir loyihaning bir necha
vaqtdagi holati yonma-yon turadi — 10 soat oldin, 5 soat oldin, 2 soat
oldin, hozir. Shu tufayli "kim harakat qilyapti" bir qarashda ko'rinadi,
bitta "oxirgi yarim soat" raqamiga qarab taxmin qilish shart emas.

Rang faqat ma'no tashiydi: sur'at qanchalik yuqori bo'lsa, shuncha issiq.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageDraw

from renderer import FONTS_DIR, MINUS, fmt_votes  # noqa: F401
from PIL import ImageFont

# --- Ranglar ---
BG           = "#FFFFFF"
INK          = "#12181F"
INK_MID      = "#5A6672"
INK_SOFT     = "#93A0AB"
LINE         = "#E8EDF1"
HEADER_BG    = "#F4F7F9"
CUTOFF_LINE  = "#C0392B"

US_BG        = "#E4F0F8"     # asosiy loyihamiz qatori
US2_BG       = "#E6F3EC"     # ikkinchi loyihamiz qatori

# Sur'at darajalari: chegara (ovoz/soat), rang, yorliq
TIERS = [
    (70, "#B0281B", "SHIDDAT"),
    (35, "#C2711A", "TEZ"),
    (10, "#3E7A54", "O'RTA"),
    (2,  "#6E7C88", "SEKIN"),
    (-10**9, "#A0AAB4", "TURIBDI"),
]

SCALE   = 2
WIDTH   = 1000
PAD     = 36
ROW_H   = 40
HEAD_H  = 52
TITLE_H = 34
GAP     = 22
RADIUS  = 12


@dataclass
class TrendRow:
    rank: int
    name: str
    past: list[int | None]      # eski nuqtalar, chapdan o'ngga
    now: int
    rate: float                 # ovoz/soat
    is_us: bool = False
    is_us2: bool = False
    cutoff_after: bool = False


def tier(rate: float) -> tuple[str, str]:
    for threshold, color, label in TIERS:
        if rate >= threshold:
            return color, label
    return TIERS[-1][1], TIERS[-1][2]


def _font(bold: bool, size: int):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(str(FONTS_DIR / name), size * SCALE)


def render_trend(rows: list[TrendRow], labels: list[str], timestamp: str,
                 subtitle: str) -> bytes:
    """
    rows   — jadval qatorlari
    labels — eski nuqtalar sarlavhalari, masalan ["10 soat", "5 soat", "2 soat"]
    """
    s = SCALE
    f_title  = _font(True, 20)
    f_sub    = _font(False, 14)
    f_head   = _font(False, 12)
    f_cell   = _font(False, 15)
    f_cell_b = _font(True, 15)
    f_tag    = _font(True, 11)

    table_h = HEAD_H + ROW_H * len(rows)
    total_h = PAD + TITLE_H + GAP + table_h + PAD

    img = Image.new("RGB", (WIDTH * s, total_h * s), BG)
    d = ImageDraw.Draw(img)

    # ---------- sarlavha ----------
    d.text((PAD * s, PAD * s), timestamp, font=f_title, fill=INK)
    d.text((PAD * s, (PAD + 22) * s), subtitle, font=f_sub, fill=INK_MID)

    # ---------- ustunlar ----------
    left  = PAD * s
    right = (WIDTH - PAD) * s
    top   = (PAD + TITLE_H + GAP) * s
    width = right - left

    n_past = len(labels)
    col_rank = left + 34 * s
    col_name = left + 50 * s
    # o'ngdan chapga: sur'at, hozir, so'ng eski nuqtalar
    col_rate  = right - 20 * s          # raqam, o'ngga tekislangan
    col_label = right - 68 * s          # yorliq, o'ngga tekislangan
    col_now   = right - 150 * s
    step      = 92 * s
    col_past = [col_now - step * (n_past - i) for i in range(n_past)]

    layer = Image.new("RGB", (width, table_h * s), BG)
    ld = ImageDraw.Draw(layer)
    ld.rectangle([0, 0, width, HEAD_H * s], fill=HEADER_BG)

    def y_of(i: int) -> int:
        return HEAD_H * s + i * ROW_H * s

    # qator fonlari va ajratuvchilar
    for i, r in enumerate(rows):
        y0 = y_of(i)
        if r.is_us:
            ld.rectangle([0, y0, width, y0 + ROW_H * s], fill=US_BG)
        elif r.is_us2:
            ld.rectangle([0, y0, width, y0 + ROW_H * s], fill=US2_BG)
        if i:
            cut = rows[i - 1].cutoff_after
            ld.line([(0, y0), (width, y0)],
                    fill=CUTOFF_LINE if cut else LINE,
                    width=max(2, s) if cut else max(1, s // 2))

    ox = left
    hy = (HEAD_H * s - 14 * s) // 2
    ld.text((col_name - ox, hy), "MAHALLA", font=f_head, fill=INK_SOFT)
    for i, lab in enumerate(labels):
        ld.text((col_past[i] - ox, hy), lab, font=f_head, fill=INK_SOFT, anchor="ra")
    ld.text((col_now - ox, hy), "HOZIR", font=f_head, fill=INK_SOFT, anchor="ra")
    ld.text((col_rate - ox, hy), "OVOZ/SOAT", font=f_head, fill=INK_SOFT, anchor="ra")

    for i, r in enumerate(rows):
        font = f_cell_b if (r.is_us or r.is_us2) else f_cell
        y = y_of(i) + (ROW_H * s - 15 * s) // 2
        color, label = tier(r.rate)

        ld.text((col_rank - ox, y), str(r.rank), font=font,
                fill=INK if (r.is_us or r.is_us2) else INK_SOFT, anchor="ra")

        name = r.name
        if r.is_us:
            name += "  ★"
        elif r.is_us2:
            name += "  ☆"
        ld.text((col_name - ox, y), name, font=font, fill=INK)

        for k, v in enumerate(r.past):
            txt = fmt_votes(v) if v is not None else "—"
            ld.text((col_past[k] - ox, y), txt, font=f_cell,
                    fill=INK_SOFT if v is not None else LINE, anchor="ra")

        ld.text((col_now - ox, y), fmt_votes(r.now), font=font, fill=INK, anchor="ra")

        # sur'at — rangli, yonida yorliq
        rate_txt = f"+{r.rate:.0f}"
        ld.text((col_rate - ox, y), rate_txt, font=f_cell_b, fill=color, anchor="ra")
        ld.text((col_label - ox, y), label, font=f_tag, fill=color, anchor="ra")

    mask = Image.new("L", (width, table_h * s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, width - 1, table_h * s - 1], radius=RADIUS * s, fill=255)
    img.paste(layer, (left, top), mask)
    d.rounded_rectangle([left, top, right - 1, top + table_h * s - 1],
                        radius=RADIUS * s, outline="#D9E0E6", width=max(1, s // 2))

    out = img.resize((WIDTH, total_h), Image.LANCZOS)
    buf = BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
