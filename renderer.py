"""
Reyting jadvalini PNG rasm sifatida chizadi (Pillow).

NEGA PILLOW, HTML+PLAYWRIGHT EMAS
---------------------------------
Jadval juda oddiy — 4 ta ustun, tekis qatorlar. CSS ning ustunligi bu yerda
deyarli yo'q, lekin Playwright yo'li quyidagilarni qimmatga tushiradi:

  * Docker image ~500 MB ga o'sadi (chromium + kutubxonalari)
  * har bir rasm uchun brauzer ishga tushirish ~1-2 soniya va ~200 MB RAM
  * render natijasi chromium versiyasiga bog'liq bo'lib qoladi

Pillow bilan esa raqamlar piksel darajasida o'lchanib joylashtiriladi —
`tabular-nums` shrift xususiyatiga tayanish shart emas, o'ng tomonlama
tekislash aniq chiqadi. Natija har qanday muhitda bir xil.

Playwright loyihada baribir bor (scraper uchun zaxira), lekin rasm chizish
uchun ishlatilmaydi.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(__file__).resolve().parent
FONTS_DIR = BASE_DIR / "fonts"

# --- Ranglar ---
BG = "#FFFFFF"
BORDER = "#E5E5E5"
HEADER_BG = "#F5F5F5"
SEPARATOR = "#EEEEEE"
TEXT = "#111111"
TEXT_MUTED = "#8A8A8A"
TEXT_HEADER = "#6B6B6B"
TEXT_DIFF = "#3D3D3D"      # bizdan pastdagilarning "Farq" ustuni
OUR_ROW_BG = "#FAFAFA"
CUTOFF = "#D14343"     # budjet chegarasi chizig'i

#: Chegaradan keyin nechta "sig'magan" loyiha ko'rsatiladi
BELOW_CUTOFF = 2

# --- O'lchamlar (1x, keyin SCALE ga ko'paytiriladi) ---
SCALE = 2                  # retina: 2x chizib, so'ng 1x ga siqiladi
WIDTH = 920
PAD = 40                   # sahifa cheti
ROW_H = 48
HEADER_H = 46
RADIUS = 12
TABLE_PAD_X = 20           # jadval ichidagi chap/o'ng bo'shliq
TITLE_GAP = 26             # sarlavha va jadval orasi

MINUS = "−"           # U+2212 MINUS SIGN


@dataclass
class Row:
    rank: int
    name: str
    votes: int
    diff: int | None       # None = bu biz
    is_us: bool = False
    #: Shu qatordan keyin budjet tugaydi — jadvalda qizil chiziq chiziladi.
    #: G'oliblar soni budjetga qarab o'zgaradi (26.08.2026 da 15 -> 19 bo'ldi).
    cutoff_after: bool = False


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    path = FONTS_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"Shrift topilmadi: {path}\n"
            "fonts/ papkasiga DejaVuSans.ttf va DejaVuSans-Bold.ttf ni qo'ying "
            "(README ga qarang)."
        )
    return ImageFont.truetype(str(path), size * SCALE)


def fmt_votes(n: int) -> str:
    """7441 -> '7 441'"""
    return f"{n:,}".replace(",", " ")


def fmt_diff(diff: int | None) -> str:
    if diff is None:
        return "BIZ"
    if diff < 0:
        return f"{MINUS}{fmt_votes(abs(diff))}"
    return f"+{fmt_votes(diff)}"


# O'zbek kirill -> lotin. Rasm kirillcha qoladi (saytdagidek), lekin caption
# lotinda yoziladi — guruhda o'qish osonroq.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "x", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh",
    "ъ": "'", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ў": "o'", "қ": "q", "ғ": "g'", "ҳ": "h",
}


def sanitize(text: str, limit: int = 40) -> str:
    """
    Saytdan kelgan matnni xabarga qo'yishdan oldin tozalaydi.

    Mahalla nomlarini odamlar kiritadi, ya'ni ular ishonchsiz manba.
    Nom katta guruhga yuboriladigan xabarga tushgani uchun havola,
    boshqaruv belgilari va haddan tashqari uzunlik olib tashlanadi.
    """
    if not text:
        return "—"
    cleaned = "".join(ch for ch in text if ch.isprintable() and ch not in "<>")
    cleaned = re.sub(r"(https?://\S+|www\.\S+|t\.me/\S+|@\w+)", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return "—"
    return cleaned[:limit].strip()


def to_latin(text: str) -> str:
    """Кирилл -> lotin (Юқори Санчиқул -> Yuqori Sanchiqul). Matn tozalanadi."""
    text = sanitize(text)
    out = []
    for ch in text:
        lower = ch.lower()
        mapped = _TRANSLIT.get(lower)
        if mapped is None:
            out.append(ch)
        elif ch.isupper() and mapped:
            out.append(mapped[0].upper() + mapped[1:])
        else:
            out.append(mapped)
    return "".join(out)


def disambiguate(names: list[str]) -> list[str]:
    """Bir xil mahalla nomi bir necha marta uchrasa (2), (3) qo'shadi."""
    seen: dict[str, int] = {}
    out = []
    for name in names:
        seen[name] = seen.get(name, 0) + 1
        out.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return out


def _truncate(draw: ImageDraw.ImageDraw, text: str, font, max_px: int) -> str:
    if draw.textlength(text, font=font) <= max_px:
        return text
    ellipsis = "…"
    while text and draw.textlength(text + ellipsis, font=font) > max_px:
        text = text[:-1]
    return text.rstrip() + ellipsis


def render(rows: list[Row], timestamp: str, district: str) -> bytes:
    """Jadval rasmini chizadi va PNG baytlarini qaytaradi."""
    f_title = _font(True, 21)
    f_subtitle = _font(False, 21)
    f_head = _font(False, 13)
    f_cell = _font(False, 16)
    f_cell_b = _font(True, 16)

    s = SCALE
    title_h = 30
    table_h = HEADER_H + ROW_H * len(rows)
    total_h = PAD + title_h + TITLE_GAP + table_h + PAD

    canvas = Image.new("RGB", (WIDTH * s, total_h * s), BG)
    draw = ImageDraw.Draw(canvas)

    # ---------- Sarlavha ----------
    tx, ty = PAD * s, PAD * s
    draw.text((tx, ty), timestamp, font=f_title, fill=TEXT)
    tx += draw.textlength(timestamp, font=f_title)
    draw.text((tx, ty), f"  — {district}", font=f_subtitle, fill=TEXT_MUTED)

    # ---------- Jadval geometriyasi ----------
    t_left = PAD * s
    t_right = (WIDTH - PAD) * s
    t_top = (PAD + title_h + TITLE_GAP) * s
    t_bottom = t_top + table_h * s
    t_width = t_right - t_left

    # Ustun chegaralari (jadval ichida)
    inner_l = t_left + TABLE_PAD_X * s
    inner_r = t_right - TABLE_PAD_X * s
    col_rank_r = inner_l + 34 * s          # "#" o'ngga tekislanadi
    col_name_l = inner_l + 56 * s
    col_diff_r = inner_r                    # "Farq" o'ngga tekislanadi
    col_votes_r = inner_r - 130 * s         # "Ovoz" o'ngga tekislanadi
    name_max_px = col_votes_r - 90 * s - col_name_l

    # Jadval qatlami — burchaklarni yumaloqlash uchun alohida chiziladi
    layer = Image.new("RGB", (t_width, table_h * s), BG)
    ld = ImageDraw.Draw(layer)

    # Sarlavha qatori foni
    ld.rectangle([0, 0, t_width, HEADER_H * s], fill=HEADER_BG)

    def cell_y(row_index: int) -> int:
        return HEADER_H * s + row_index * ROW_H * s

    # Bizning qator foni + qatorlar orasidagi ajratuvchi chiziqlar
    for i, row in enumerate(rows):
        y0 = cell_y(i)
        if row.is_us:
            ld.rectangle([0, y0, t_width, y0 + ROW_H * s], fill=OUR_ROW_BG)
        if i > 0:
            prev_cut = rows[i - 1].cutoff_after
            ld.line([(0, y0), (t_width, y0)],
                    fill=CUTOFF if prev_cut else SEPARATOR,
                    width=max(2, s) if prev_cut else max(1, s // 2))

    # Sarlavha qatori matni
    ox = t_left  # layer ichidagi koordinatalar uchun ofset
    head_y = (HEADER_H * s - _text_h(ld, "Mahalla", f_head)) // 2
    ld.text((col_rank_r - ox, head_y), "#", font=f_head, fill=TEXT_HEADER, anchor="ra")
    ld.text((col_name_l - ox, head_y), "Mahalla", font=f_head, fill=TEXT_HEADER)
    ld.text((col_votes_r - ox, head_y), "Ovoz", font=f_head, fill=TEXT_HEADER, anchor="ra")
    ld.text((col_diff_r - ox, head_y), "Farq", font=f_head, fill=TEXT_HEADER, anchor="ra")

    # Qatorlar
    for i, row in enumerate(rows):
        font = f_cell_b if row.is_us else f_cell
        y = cell_y(i) + (ROW_H * s - _text_h(ld, "0", font)) // 2

        rank_color = TEXT if row.is_us else TEXT_MUTED
        ld.text((col_rank_r - ox, y), str(row.rank), font=font, fill=rank_color, anchor="ra")

        name = _truncate(ld, row.name, font, name_max_px)
        ld.text((col_name_l - ox, y), name, font=font, fill=TEXT)

        ld.text((col_votes_r - ox, y), fmt_votes(row.votes), font=font, fill=TEXT, anchor="ra")

        # Farq: bizdan yuqoridagilar qalin qora, pastdagilar oddiy
        if row.is_us:
            diff_font, diff_color = f_cell_b, TEXT
        elif row.diff is not None and row.diff < 0:
            diff_font, diff_color = f_cell_b, TEXT
        else:
            diff_font, diff_color = f_cell, TEXT_DIFF
        ld.text((col_diff_r - ox, y), fmt_diff(row.diff),
                font=diff_font, fill=diff_color, anchor="ra")

    # Yumaloq burchakli niqob orqali joylashtirish
    mask = Image.new("L", (t_width, table_h * s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, t_width - 1, table_h * s - 1], radius=RADIUS * s, fill=255
    )
    canvas.paste(layer, (t_left, t_top), mask)

    # Tashqi chegara
    draw.rounded_rectangle(
        [t_left, t_top, t_right - 1, t_bottom - 1],
        radius=RADIUS * s, outline=BORDER, width=max(1, s // 2),
    )

    # 2x -> 1x siqish (matn tiniq chiqadi)
    final = canvas.resize((WIDTH, total_h), Image.LANCZOS)
    buf = BytesIO()
    final.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _text_h(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[3] - box[1]


def build_rows(items, our_id: str, top_n: int, winners: int | None = None) -> list[Row]:
    """
    Skreyperdan kelgan (ovoz bo'yicha saralangan) ro'yxatdan jadval qatorlarini
    yasaydi. Agar bizning loyiha TOP-N dan tashqarida bo'lsa, u oxiriga qo'shiladi.
    """
    names = disambiguate([it.quarter for it in items])
    our_index = next((i for i, it in enumerate(items) if it.project_id == our_id), None)
    our_votes = items[our_index].votes if our_index is not None else None

    def make(i: int) -> Row:
        it = items[i]
        is_us = i == our_index
        diff = None if is_us else (our_votes - it.votes if our_votes is not None else 0)
        return Row(rank=i + 1, name=names[i], votes=it.votes, diff=diff, is_us=is_us)

    # Budjetga sig'adigan hamma loyiha ko'rinsin, ustiga chegaradan keyingi
    # ikkitasi ham — kim sig'may qolgani ko'rinib tursin.
    shown = max(top_n, winners + BELOW_CUTOFF) if winners else top_n
    shown = min(shown, len(items))

    rows = [make(i) for i in range(shown)]
    if our_index is not None and our_index >= shown:
        rows.append(make(our_index))

    # Chegara chizig'i — oxirgi g'olibdan keyin
    if winners and 0 < winners < len(rows):
        rows[winners - 1].cutoff_after = True
    return rows


if __name__ == "__main__":
    # Dizaynni tez tekshirish uchun sinov ma'lumoti
    demo = [
        Row(1, "Юқори Санчиқул", 7443, -274),
        Row(2, "Навбоғ", 7373, -204),
        Row(3, "Қуйи Тегана", 7169, None, is_us=True),
        Row(4, "Украч", 6447, 722),
        Row(5, "Деҳқонобод", 6433, 736),
    ]
    Path("test_output.png").write_bytes(render(demo, "26.08.2026, 17:10", "Urgut tumani"))
    print("test_output.png saqlandi")
