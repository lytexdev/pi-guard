"""The words of this home, so spoken requests come out right.

Speech models know everyday language but not "Wintergarten-Kamera", "Stehlampe Flur" or
"pi-guard". `words` collects the names this home uses; `correct` puts a nearly-right word back
to the name (offline, no tokens), and `hint` is the short list a cloud speech model may get as
context. People's names are used only on the Pi, never in the hint: they are nobody else's
business.
"""

from __future__ import annotations

import difflib
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pi_guard.app import App

# Words of pi-guard itself, which speech models spell in their own ways.
BASE = ("pi-guard", "Pi-hole", "VPN", "WireGuard", "Alarm", "Zuhause", "Abwesend", "Kamera",
        "Aufnahme", "Mikrofon", "Notfall")  # fmt: skip
FIXED = {"pi guard": "pi-guard", "pie guard": "pi-guard", "piguard": "pi-guard",
         "pi hole": "Pi-hole", "pie hole": "Pi-hole", "wire guard": "WireGuard"}  # fmt: skip
MIN_WORD = 4  # short words ("an", "aus", "Bad") are too easy to mistake for others
CUTOFF = 0.84
MAX_HINT = 60


def words(app: App, people: bool = False) -> list[str]:
    """Names of cameras, rooms, devices and commands here; with `people`, also persons."""
    found: list[str] = []
    for camera_id, pipeline in app.cameras.items():
        found += [pipeline.camera.name or "", pipeline.camera.room or "", camera_id]
    addons = app.addons
    if addons.smarthome is not None:
        found += [live.device.name for live in addons.smarthome.live.values()]
    if addons.remote is not None:
        found += [action.name for action in addons.remote.actions.values()]
    if addons.agents is not None:
        found += [str(agent.get("name", "")) for agent in addons.agents.list()]
    faces = getattr(getattr(app, "analysis", None), "faces", None)
    if people and faces is not None:
        found += [name for name, _, _ in faces.store.people()]
    seen: dict[str, str] = {}
    for name in [*BASE, *found]:
        name = " ".join(str(name).replace("_", " ").split())
        if 2 < len(name) <= 40:
            seen.setdefault(name.casefold(), name)
    return list(seen.values())


def hint(app: App) -> str:
    """Context for a cloud speech model: the names, never people's."""
    return ", ".join(words(app)[:MAX_HINT])


def correct(text: str, vocabulary: list[str]) -> str:
    """Nearly-right words back to the names of this home. Only close matches change."""
    for wrong, right in FIXED.items():
        text = re.sub(rf"\b{re.escape(wrong)}\b", right, text, flags=re.IGNORECASE)
    names = [n for n in vocabulary if len(n) >= MIN_WORD]
    if not names or not text:
        return text
    tokens = text.split(" ")
    by_length: dict[int, dict[str, str]] = {}
    for name in names:
        by_length.setdefault(len(name.split()), {})[name.casefold()] = name
    out: list[str] = []
    i = 0
    while i < len(tokens):
        for size in sorted(by_length, reverse=True):
            window = tokens[i : i + size]
            if len(window) < size:
                continue
            bare = [w.strip(".,!?;:") for w in window]
            phrase = " ".join(bare).casefold()
            if len(phrase) < MIN_WORD:
                continue
            match = difflib.get_close_matches(phrase, by_length[size], n=1, cutoff=CUTOFF)
            if match:  # right already, or nearly: either way these words are one name
                tail = window[-1][len(bare[-1]) :]
                exact = match[0] == phrase
                out.append(" ".join(window) if exact else by_length[size][match[0]] + tail)
                i += size
                break
        else:
            out.append(tokens[i])
            i += 1
    return " ".join(out)
