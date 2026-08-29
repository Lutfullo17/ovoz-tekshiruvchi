"""
Xabar matnini tuzish — faqat o'lchangan raqamlar.

Bu yerda taxmin, prognoz va sun'iy intellekt matni yo'q. Guruhga faqat
bo'lgan narsa yoziladi: loyiha falon vaqt oldin qanday edi, hozir qanday,
soatiga qancha ovoz olyapti.
"""
from __future__ import annotations

from config import CONFIG
from renderer import fmt_votes
from trend_render import tier


def _block(title: str, m: dict, labels: list[str]) -> str:
    """Bitta loyihaning vaqt bo'yicha holati."""
    lines = [title]
    for lab, val in zip(labels, m["past"]):
        if val is not None:
            lines.append(f"{lab} oldin:  {fmt_votes(val)}")
    lines.append(f"Hozir:  {fmt_votes(m['now'])} ovoz · {m['rank']}-o'rin")

    _, label = tier(m["rate"])
    if m["rate"] >= 1:
        lines.append(f"Sur'at: soatiga {m['rate']:.0f} ovoz · {label}")
    else:
        lines.append("Sur'at: yangi ovoz kelmayapti")
    return "\n".join(lines)


def build_caption(meta: dict, labels: list[str], items, winners: int | None,
                  now, movers: int = 4) -> str:
    """
    meta   — trend_data.collect() dan
    labels — ustun sarlavhalari ("37 soat", ...)
    """
    blocks = []

    ours = meta.get(CONFIG.project_id)
    if ours:
        blocks.append(_block("📍 ASOSIY LOYIHAMIZ", ours, labels))

    second = meta.get(CONFIG.second_project_id)
    if second:
        blocks.append(_block("📍 IKKINCHI LOYIHAMIZ", second, labels))

    # Eng tez o'sayotganlar — bizni tashqarida qoldirmasdan
    fast = sorted(
        (m for pid, m in meta.items() if m["rate"] >= 1),
        key=lambda m: m["rate"], reverse=True)[:movers]
    if fast:
        rows = [f"{m['rank']}. {m['name']} — soatiga {m['rate']:.0f}"
                for m in fast]
        blocks.append("🔥 ENG TEZ O'SAYOTGANLAR\n" + "\n".join(rows))

    # Bizdan oldindagi va ortdagi eng yaqin ikkitasi
    if ours:
        rank = ours["rank"]
        ahead = items[max(0, rank - 3):rank - 1]
        behind = items[rank:rank + 2]
        from renderer import to_latin
        if ahead:
            rows = [f"{to_latin(x.quarter)} — {fmt_votes(x.votes - ours['now'])} ovoz ko'p"
                    for x in reversed(ahead)]
            blocks.append("⬆️ BIZDAN OLDINDA\n" + "\n".join(rows))
        if behind:
            rows = [f"{to_latin(x.quarter)} — {fmt_votes(ours['now'] - x.votes)} ovoz kam"
                    for x in behind]
            blocks.append("⬇️ BIZDAN ORTDA\n" + "\n".join(rows))

    if winners:
        edge = items[winners - 1]
        if ours and ours["rank"] <= winners:
            blocks.append(f"✅ G'oliblar ichidamiz\n"
                          f"Chegara — {winners}-o'rin ({fmt_votes(edge.votes)} ovoz)")
        else:
            blocks.append(f"⚠️ Chegaradan tashqaridamiz\n"
                          f"Chegara — {winners}-o'rin ({fmt_votes(edge.votes)} ovoz)")

    blocks.append(f"🕘 {now:%d.%m.%Y %H:%M}")
    return "\n\n".join(blocks)
