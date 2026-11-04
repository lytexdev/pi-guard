"""User-facing text in English and German.

Keys are flat (`menu.title`). Every key must exist in every language; the test
suite enforces this, including identical placeholders.
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import cache
from importlib import resources
from pathlib import Path
from zoneinfo import ZoneInfo

LANGUAGES = ["en", "de"]  # extended by load_extra()
_extra: dict[str, dict[str, str]] = {}


def load_extra(directory: Path) -> None:
    """Languages added by the language add-on, stored as <code>.json in `directory`."""
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        try:
            _extra[path.stem] = json.loads(path.read_text("utf-8"))
        except ValueError:
            continue
        if path.stem not in LANGUAGES:
            LANGUAGES.append(path.stem)


def remove_extra(code: str) -> None:
    _extra.pop(code, None)
    if code in LANGUAGES and code not in ("en", "de"):
        LANGUAGES.remove(code)


@cache
def _builtin(language: str) -> dict[str, str]:
    text = resources.files(__package__).joinpath(f"{language}.json").read_text("utf-8")
    data: dict[str, str] = json.loads(text)
    return data


def catalog(language: str) -> dict[str, str]:
    if language in _extra:
        return {**_builtin("en"), **_extra[language]}  # new texts stay readable until retranslated
    return _builtin(language)


def t(key: str, language: str, /, **values: object) -> str:
    lang = language if language in LANGUAGES else "en"
    template = catalog(lang).get(key) or catalog("en")[key]
    return template.format(**values) if values else template


def format_time(moment: datetime, tz: ZoneInfo, language: str, with_date: bool = False) -> str:
    local = moment.astimezone(tz)
    today = datetime.now(tz).date()
    clock = local.strftime("%H:%M")
    if not with_date and local.date() == today:
        return clock
    date = local.strftime("%d.%m.%Y" if language == "de" else "%Y-%m-%d")
    return f"{date} {clock}"


def format_duration(seconds: float, language: str) -> str:
    seconds = int(seconds)
    if seconds < 90:
        return t("unit.seconds", language, n=seconds)
    if seconds < 90 * 60:
        return t("unit.minutes", language, n=round(seconds / 60))
    if seconds < 36 * 3600:
        return t("unit.hours", language, n=round(seconds / 3600))
    return t("unit.days", language, n=round(seconds / 86400))
