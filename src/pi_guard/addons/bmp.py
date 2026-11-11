"""The German federal medication plan (Bundeseinheitlicher Medikationsplan, BMP).

Its Data Matrix code holds a short XML text: `<MP>` with the patient `<P>`, the issuer `<A>`,
observations `<O>` and sections `<S>` of medicines `<M>` (with ingredients `<W>`) or free
lines `<X>`. A scanner app on the phone reads the code and gives this text; pasted into the
dashboard, it fills the medicine list. The other way, a sheet gives such a text to keep or
to hand on. Drawing the Data Matrix code itself is not done here.

Only what the sheet holds is read: names, strengths, doses, units, reasons and notes.
Section codes follow the specification where they are known (412 regular, 411 as needed,
414 self-medication); a free section title with "Selbst" or "Bedarf" counts as well.
Real plans from a pharmacy or practice were not tried yet, only the published structure.
"""

from __future__ import annotations

import secrets
import xml.etree.ElementTree as ET
from typing import Any

from pi_guard.addons.medical import MED_FIELDS, MedicalError

MAX_BYTES = 16_000
SECTION_REGULAR, SECTION_AS_NEEDED, SECTION_SELF = "412", "411", "414"
# BMP attribute -> field of a medicine in the sheet.
_M = {"a": "name", "fd": "form", "m": "morning", "d": "noon", "v": "evening", "h": "night",
      "t": "schedule", "dud": "unit", "r": "reason", "i": "note"}  # fmt: skip


def _section(element: ET.Element) -> tuple[str, str]:
    """(kind, source) of the medicines in a section."""
    code, title = element.get("c", ""), element.get("t", "").casefold()
    source = "self" if code == SECTION_SELF or "selbst" in title or "self" in title else "doctor"
    needed = code == SECTION_AS_NEEDED or "bedarf" in title or "needed" in title
    return ("as_needed" if needed else "regular"), source


def parse(text: str) -> dict[str, Any]:
    """The medicines (and allergies, if given) of a plan. Raises MedicalError."""
    raw = text.strip().encode()
    if not raw or len(raw) > MAX_BYTES or b"<!" in raw:  # no DOCTYPE, no entities
        raise MedicalError("web.md_bmp_bad")
    try:
        root = ET.fromstring(raw)  # noqa: S314 - small, without DOCTYPE, checked above
    except ET.ParseError:
        raise MedicalError("web.md_bmp_bad") from None
    if root.tag != "MP":
        raise MedicalError("web.md_bmp_bad")
    meds: list[dict[str, str]] = []
    supplements: list[dict[str, str]] = []
    for section in root.iter("S"):
        kind, source = _section(section)
        title = section.get("t", "").casefold()
        if "nahrungserg" in title or "supplement" in title:
            for m in section.iter("M"):
                item = {"name": m.get("a", "")[:80], "dose": m.get("t", "")[:60],
                        "note": m.get("i", "")[:200]}  # fmt: skip
                if item["name"]:
                    supplements.append({k: v for k, v in item.items() if v})
            continue
        for m in section.iter("M"):
            med = {field: m.get(attr, "") for attr, field in _M.items() if m.get(attr)}
            ingredients = m.findall("W")
            if ingredients:
                med["ingredient"] = ", ".join(w.get("w", "") for w in ingredients if w.get("w"))
                strengths = [w.get("s", "") for w in ingredients if w.get("s")]
                if strengths:
                    med["strength"] = ", ".join(strengths)
            if not med.get("name"):
                med["name"] = med.get("ingredient") or (f"PZN {m.get('p')}" if m.get("p") else "")
            if med.get("name"):
                med = {k: v[: MED_FIELDS[k]] for k, v in med.items()}
                meds.append({**med, "kind": kind, "source": source})
    observation = root.find("O")
    allergies = observation.get("ai", "") if observation is not None else ""
    person = root.find("P")
    name = " ".join(filter(None, [person.get("g"), person.get("f")])) if person is not None else ""
    if not meds and not supplements and not allergies:
        raise MedicalError("web.md_bmp_empty")
    return {"meds": meds, "supplements": supplements, "allergies": allergies, "name": name}


def build(record: dict[str, Any]) -> str:
    """A BMP text for the sheet's medicines, with free section titles."""
    root = ET.Element("MP", {"v": "026", "U": secrets.token_hex(16).upper(), "l": "de-DE"})
    given, _, family = str(record.get("name", "")).rpartition(" ")
    person = {"g": given or family, "f": family if given else ""}
    if record.get("birthday"):
        person["b"] = str(record["birthday"]).replace("-", "")
    ET.SubElement(root, "P", {k: v for k, v in person.items() if v})
    ET.SubElement(root, "A", {"n": "pi-guard"})
    if record.get("allergies") or record.get("weight_kg") or record.get("height_cm"):
        observation = {
            "ai": " ".join(str(record.get("allergies", "")).split())[:100],
            "w": str(record.get("weight_kg", "")),
            "h": str(record.get("height_cm", "")),
        }
        ET.SubElement(root, "O", {k: v for k, v in observation.items() if v})
    groups = (  # title, as needed, self-medication
        ("Dauermedikation", False, False),
        ("Bedarfsmedikation", True, False),
        ("Selbstmedikation", False, True),
        ("Selbstmedikation bei Bedarf", True, True),
    )
    for title, needed, own in groups:
        meds = [
            m for m in record.get("meds", [])
            if (m.get("kind") == "as_needed") == needed and (m.get("source") == "self") == own
        ]  # fmt: skip
        if not meds:
            continue
        section = ET.SubElement(root, "S", {"t": title})
        for med in meds:
            attrs = {attr: str(med.get(field, "")) for attr, field in _M.items()}
            element = ET.SubElement(section, "M", {k: v for k, v in attrs.items() if v})
            if med.get("ingredient") or med.get("strength"):
                ingredient = {"w": med.get("ingredient") or med.get("name", ""),
                              "s": med.get("strength", "")}  # fmt: skip
                ET.SubElement(element, "W", {k: v for k, v in ingredient.items() if v})
    supplements = record.get("supplements", [])
    if supplements:
        section = ET.SubElement(root, "S", {"t": "Nahrungsergänzung"})
        for item in supplements:
            attrs = {"a": item.get("name", ""), "t": item.get("dose", ""),
                     "i": item.get("note", "")}  # fmt: skip
            ET.SubElement(section, "M", {k: v for k, v in attrs.items() if v})
    return ET.tostring(root, encoding="unicode")
