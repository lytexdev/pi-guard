"""User-facing text in English and German.

Keys are flat (`menu.title`). Every key must exist in every language; the test
suite enforces this, including identical placeholders.
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import cache
from importlib import resources
from zoneinfo import ZoneInfo

LANGUAGES = ("en", "de")


@cache
def catalog(language: str) -> dict[str, str]:
    text = resources.files(__package__).joinpath(f"{language}.json").read_text("utf-8")
    data: dict[str, str] = json.loads(text)
    return data


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
