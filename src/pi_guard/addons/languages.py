"""More languages through DeepL: translates all texts once and stores them on this device."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path

import httpx

from pi_guard.i18n import catalog, load_extra

log = logging.getLogger(__name__)

BATCH = 50  # DeepL's limit per request
TIMEOUT = 60
# DeepL code, and the name to show.
LANGUAGES = {
    "fr": "Français", "es": "Español", "it": "Italiano", "nl": "Nederlands", "pl": "Polski",
    "pt-pt": "Português", "tr": "Türkçe", "ru": "Русский", "uk": "Українська", "cs": "Čeština",
    "sv": "Svenska", "da": "Dansk", "el": "Ελληνικά", "ro": "Română", "hu": "Magyar",
}  # fmt: skip
_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def protect(text: str) -> str:
    """Placeholders like {name} must survive translation unchanged."""
    return _PLACEHOLDER.sub(
        lambda m: f"<x>{m.group(0)}</x>", text.replace("&", "&amp;").replace("<", "&lt;")
    )


def restore(text: str) -> str:
    return re.sub(r"</?x>", "", text).replace("&lt;", "<").replace("&amp;", "&")


def checked(original: str, translated: str) -> str:
    """A translation that lost a placeholder would crash at runtime; English is better then."""
    wanted = sorted(_PLACEHOLDER.findall(original))
    return translated if sorted(_PLACEHOLDER.findall(translated)) == wanted else original


async def translate_all(
    code: str, key: str, directory: Path, progress: Callable[[int, int], None]
) -> int:
    if code not in LANGUAGES:
        raise ValueError(code)
    host = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
    source = catalog("en")
    keys = list(source)
    result: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for start in range(0, len(keys), BATCH):
            part = keys[start : start + BATCH]
            response = await client.post(
                f"https://{host}/v2/translate",
                headers={"Authorization": f"DeepL-Auth-Key {key}"},
                json={"text": [protect(source[k]) for k in part], "source_lang": "EN",
                      "target_lang": code.upper(), "tag_handling": "xml", "ignore_tags": ["x"]},
            )  # fmt: skip
            response.raise_for_status()
            for name, item in zip(part, response.json()["translations"], strict=True):
                result[name] = checked(source[name], restore(item["text"]))
            progress(min(start + BATCH, len(keys)), len(keys))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{code}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    load_extra(directory)
    return len(result)


def installed(directory: Path) -> list[str]:
    return sorted(p.stem for p in directory.glob("*.json")) if directory.is_dir() else []
