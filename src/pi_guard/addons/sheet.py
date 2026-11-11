"""One person's emergency sheet as text lines and as a PDF, for saving, printing or sending.

The PDF is written here, without a library: A4, the PDF's own Helvetica, Western European
characters (Windows-1252). Markdown is shown as plain text: headings bold, lists with bullets,
the marks themselves left out. Danger comes first, as on the rescuer's page.
"""

from __future__ import annotations

import re
import textwrap
import zlib
from datetime import date
from typing import Any

from pi_guard.addons.medical import ORGAN_DONOR
from pi_guard.i18n import t

# What can harm the person if nobody knows it, then the facts, then the rest.
ALERTS = ("allergies", "conditions", "implants")
FACTS = ("birthday", "blood_type", "height_cm", "weight_kg", "organ_donor", "phone", "email")
MORE = ("medication_plan", "contacts", "doctor", "insurance", "notes")
DOSES = ("morning", "noon", "evening", "night")

_MARKS = re.compile(r"(\*\*|__|`|~~)")
_ITALIC = re.compile(r"(?<![\w*])[*_]([^*_]+)[*_](?![\w*])")
_LIST = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?")


def _plain(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    return _ITALIC.sub(r"\1", _MARKS.sub("", text)).strip()


def markdown_lines(text: str) -> list[tuple[str, str]]:
    """(style, text) per line: h for headings, li for list items, p otherwise."""
    lines: list[tuple[str, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or re.fullmatch(r"\s*([-*_]\s*){3,}", raw):
            continue
        heading = re.match(r"^\s*#{1,6}\s+(.*)", raw)
        item = _LIST.match(raw)
        if heading:
            lines.append(("h", _plain(heading.group(1))))
        elif item:
            depth = len(item.group(1).expandtabs(2)) // 2
            lines.append(("li" + "+" * min(depth, 3), _plain(raw[item.end() :])))
        else:
            lines.append(("p", _plain(raw.lstrip("> "))))
    return lines


def age(birthday: str, today: date | None = None) -> int | None:
    try:
        born = date.fromisoformat(birthday)
    except ValueError:
        return None
    today = today or date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def medicine(med: dict[str, str], lang: str) -> str:
    """Name, strength and ingredient, then the dose as the BMP shows it: 1-0-1-0."""
    name = " ".join(filter(None, [med.get("name"), med.get("strength"), med.get("form")]))
    if med.get("ingredient") and med.get("ingredient") != med.get("name"):
        name += f" ({med['ingredient']})"
    if med.get("schedule"):
        dose = med["schedule"]
    elif any(med.get(k) for k in DOSES):
        dose = "-".join(med.get(k) or "0" for k in DOSES)
    else:
        dose = ""
    parts = [name, " ".join(filter(None, [dose, med.get("unit")]))]
    if med.get("kind") == "as_needed":
        parts.append(t("web.md_kind_as_needed", lang))
    parts.append(t(f"web.md_source_{med.get('source', 'doctor')}", lang))
    if med.get("reason"):
        parts.append(t("web.md_for_reason", lang, reason=med["reason"]))
    if med.get("note"):
        parts.append(med["note"])
    return " · ".join(p for p in parts if p)


def fact(record: dict[str, Any], key: str, lang: str) -> str:
    value = str(record.get(key) or "")
    if key == "birthday" and (years := age(value)) is not None:
        return f"{value} ({years})"
    if key == "organ_donor":
        known = value in ORGAN_DONOR  # free text from before there was a choice
        choice = t(f"web.md_od_{value}", lang) if value and known else value
        return " – ".join(filter(None, [choice, str(record.get("organ_donor_note") or "")]))
    return value


def lines(record: dict[str, Any], lang: str) -> list[tuple[str, str]]:
    """The sheet as (style, text): title, alert, h, p, li, kv."""
    out: list[tuple[str, str]] = [("title", str(record.get("name", "")))]
    for key in ALERTS:
        if record.get(key):
            out.append(("alert", t(f"web.md_f_{key}", lang)))
            out += markdown_lines(str(record[key]))
    if record.get("meds") or record.get("medications"):
        out.append(("alert", t("web.md_f_meds", lang)))
        out += [("li", medicine(m, lang)) for m in record.get("meds", [])]
        out += markdown_lines(str(record.get("medications") or ""))
    record = {**record, "organ_donor": record.get("organ_donor", "")}
    facts = [(k, fact(record, k, lang)) for k in FACTS]
    if any(v for _, v in facts):
        out.append(("h", t("web.md_facts", lang)))
        out += [("kv", f"{t(f'web.md_f_{k}', lang)}: {v}") for k, v in facts if v]
    if record.get("supplements"):
        out.append(("h", t("web.md_f_supplements", lang)))
        out += [("li", " · ".join(filter(None, [s.get("name"), s.get("dose"), s.get("note")])))
                for s in record["supplements"]]  # fmt: skip
    for key in MORE:
        if record.get(key):
            out.append(("h", t(f"web.md_f_{key}", lang)))
            out += markdown_lines(str(record[key]))
    return out


def as_text(record: dict[str, Any], lang: str) -> str:
    prefix = {"title": "", "alert": "\n! ", "h": "\n", "kv": "", "p": ""}
    return "\n".join(
        ("  " * (len(style) - 2) + "• " if style.startswith("li") else prefix[style]) + text
        for style, text in lines(record, lang)
    ).strip()


# --- PDF ---------------------------------------------------------------------------------

_W, _H, _MARGIN = 595, 842, 50
_STYLE = {  # font, size, gap before, indent, red
    "title": ("F2", 20, 0, 0, False), "alert": ("F2", 12, 12, 0, True),
    "h": ("F2", 12, 12, 0, False), "kv": ("F1", 10.5, 1, 0, False),
    "p": ("F1", 10.5, 2, 0, False), "li": ("F1", 10.5, 2, 10, False),
}  # fmt: skip


def _escape(text: str) -> bytes:
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def pdf(record: dict[str, Any], lang: str, footer: str = "") -> bytes:
    pages: list[list[bytes]] = [[]]
    y: float = _H - _MARGIN

    def put(font: str, size: float, x: float, text: str, red: bool = False) -> None:
        nonlocal y
        if y - size < _MARGIN + 20:
            pages.append([])
            y = _H - _MARGIN
        y -= size * 1.3
        color = b"0.7 0.1 0.1 rg " if red else b"0 0 0 rg "
        pages[-1].append(color + f"BT /{font} {size} Tf {x:.1f} {y:.1f} Td (".encode()
                         + _escape(text) + b") Tj ET")  # fmt: skip

    for style, text in lines(record, lang):
        base = style[:2] if style.startswith("li") else style
        font, size, gap, indent, red = _STYLE[base]
        indent += 12 * (len(style) - 2) if base == "li" else 0
        y -= gap
        width = int((_W - 2 * _MARGIN - indent - 10) / (size * (0.56 if font == "F2" else 0.5)))
        parts = textwrap.wrap(text, width, break_long_words=True) or [""]
        for number, part in enumerate(parts):
            bullet = "• " if base == "li" and number == 0 else "   " if base == "li" else ""
            put(font, size, _MARGIN + indent, bullet + part, red)
    if footer:
        pages[-1].append(f"BT /F1 8 Tf {_MARGIN} 30 Td (".encode() + _escape(footer) + b") Tj ET")

    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    fonts = b"<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>"
    objects += [fonts % b"Helvetica", fonts % b"Helvetica-Bold"]
    kids = []
    for content in pages:
        stream = zlib.compress(b"\n".join(content))
        objects.append(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream)
                       + stream + b"\nendstream")  # fmt: skip
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
            b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>" % (_W, _H, len(objects))
        )
        kids.append(b"%d 0 R" % len(objects))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(kids), len(kids))
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\n" % (len(objects) + 1)
    out += b"startxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)
