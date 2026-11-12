"""The tutorial: one list of chapters for the dashboard's tour and help page, the chat command
/tutorial and the assistant. Texts are in the i18n files (web.tut_*), so every place says the
same; the dashboard has the same list in js/help.js.
"""

from __future__ import annotations

import re

from pi_guard.i18n import t

# id, dashboard page, words that name the chapter in a question (folded: ä -> ae …).
CHAPTERS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("overview", "#/overview", ("uebersicht", "overview", "startseite", "zuhause und abwesend")),
    ("live", "#/live", ("live", "kamerabild", "mithoeren", "sprechen")),
    ("events", "#/events", ("ereignis", "event", "beweisvideo")),
    ("people", "#/people", ("person", "gesicht", "face", "wischen", "swipe", "wer ist das")),
    ("network", "#/network/overview",
     ("netzwerk", "network", "pi-hole", "pihole", "sperr", "block")),
    ("vpn", "#/network/vpn", ("vpn", "wireguard", "unterwegs")),
    ("kids", "#/addons/kids", ("kid protect", "kinder", "kids", "children", "jugendschutz")),
    ("emergency", "#/settings/emergency", ("notfallkontakt", "emergency contact", "sos")),
    ("medical", "#/addons/medical",
     ("notfalldaten", "medikament", "medical", "bmp", "organspende")),
    ("apps", "#/addons/mobile", ("app", "handy", "android", "desktop", "phone")),
    ("assistant", "#/addons/assistant", ("assistent", "assistant", "ki ", "sprachsteuerung")),
    ("move", "#/settings/data/move", ("umzug", "umziehen", "moving", "neuer router", "new router")),
    ("settings", "#/settings", ("einstellung", "setting", "backup", "barrierefrei", "accessib")),
)  # fmt: skip
_ASK = re.compile(
    r"\b(tutorial|anleitung|rundgang|einfuehrung|tour|wie (benutze|bediene|nutze|geht|funktioniert"
    r"|richte)|was kann|erklaer|how (do|does|can) i|how does|explain|what can)\b"
)
_WHOLE = re.compile(r"\b(tutorial|anleitung|rundgang|einfuehrung|tour)\b|was kann (pi-guard|das)")


def _fold(text: str) -> str:
    return (text.casefold().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
            .replace("ß", "ss"))  # fmt: skip


def chapter(chapter_id: str, lang: str) -> str:
    return f"{t(f'web.tut_{chapter_id}', lang)}: {t(f'web.tut_{chapter_id}_text', lang)}"


def as_text(lang: str) -> str:
    """The whole tutorial for a chat: short, one line per chapter."""
    lines = [t("web.tut_title", lang), t("web.tut_intro", lang), ""]
    lines += [f"• {chapter(c, lang)}" for c, _, _ in CHAPTERS]
    lines += ["", t("web.tut_more", lang)]
    return "\n".join(lines)


def answer(question: str, lang: str) -> str | None:
    """A chapter if the question asks how something of pi-guard works, the whole tutorial if
    it asks for one; None for anything else, which goes on to the assistant."""
    folded = _fold(question)
    if not _ASK.search(folded):
        return None
    named = [c for c, _, words in CHAPTERS
             if any(re.search(rf"\b{re.escape(w)}", folded) for w in words)]  # fmt: skip
    if named:
        return "\n\n".join(chapter(c, lang) for c in named[:2])
    if _WHOLE.search(folded) or "pi-guard" in folded or "piguard" in folded:
        return as_text(lang)
    return None
