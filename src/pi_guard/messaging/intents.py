"""Understanding everyday sentences such as "Kannst du mir ein Foto schicken?".

Deliberately simple and predictable: keyword patterns map a sentence to one of
a few harmless commands. Nothing that makes noise, records sound or changes
users can be reached this way. When a sentence fits several commands, or a
mode change is negated ("ich gehe nicht weg"), the bot asks instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pi_guard.messaging.commands.registry import normalize


def _p(*words: str) -> re.Pattern[str]:
    # Sentences are case-folded ("ß" becomes "ss"), so the patterns are too.
    return re.compile(r"\b(" + "|".join(w.casefold() for w in words) + r")\b")


# Ordered from specific to generic; a match in an earlier group hides later groups.
_GROUPS: tuple[dict[str, re.Pattern[str]], ...] = (
    {
        "sharper": _p("schärfer", "schaerfer", "unscharf", "verschwommen", "deutlicher",
                      "sharper", "blurry", "clearer"),
        "zoom": _p(r"vergrößer\w*", r"vergroesser\w*", "näher", "naeher", r"ran ?zoom\w*",
                   r"zoom\w*", "closer", "enlarge"),
        "brighter": _p("heller", "zu dunkel", "aufhellen", "brighter", "too dark"),
    },
    {
        "home": _p(r"bin\b.{0,20}\b(zu ?hause|daheim|da)", "wieder da",
                   r"i'?m (back )?home", r"i am (back )?home", "i'?m back"),
        "away": _p(r"gehe?\b.{0,20}\b(weg|los|raus)", r"bin\b.{0,20}\bweg",
                   "verlasse", "fahre weg", "urlaub", "verreise", r"i'?m leaving",
                   "i am leaving", "going out", "leaving now"),
    },
    {
        "video": _p("video", "videos", "film", "clip", "aufnahme"),
        "photo": _p("foto", "fotos", "bild", "bilder", "photo", "photos", "picture",
                    "pictures", "snapshot"),
    },
    {"photo": _p(r"zeig\w*", "show me", "schau", "look")},
    {
        "events": _p(r"was (ist )?passiert", r"ereignis\w*", "what happened", "events"),
        "status": _p("status", "funktioniert", "läuft", "laeuft", r"alles (in )?ordnung",
                     "alles ok", "working", "is everything"),
        "help": _p("hilfe", "help", "wie geht das", "was kann"),
    },
    {"thanks": _p("danke", r"danke\w*", "vielen dank", "thanks", "thank you")},
    {"menu": _p("hallo", "hi", "hey", r"guten (morgen|tag|abend)", "servus", "moin",
                "grüß gott", "hello", "good morning")},
)  # fmt: skip
_NEGATION = _p("nicht", "kein", "keine", "not", "don'?t", "no")
_SECONDS = re.compile(r"\b(\d{1,3})\s*(s|sek|sekunden|sec|seconds)?\b")
_MINUTE = _p("eine minute", "1 minute", "one minute", "a minute")


@dataclass(frozen=True)
class Understanding:
    command: str | None = None  # a command line for the registry
    options: tuple[str, ...] = ()  # intents to offer when unsure


def understand(text: str, cameras: dict[str, str]) -> Understanding:
    """Map a sentence to a command; `cameras` is {id: name} to pick up camera names."""
    sentence = normalize(text)
    if not sentence or sentence.startswith("/"):
        return Understanding()
    found: list[str] = []
    for group in _GROUPS:
        found = [intent for intent, pattern in group.items() if pattern.search(sentence)]
        if found:
            break
    if not found:
        return Understanding()
    if len(found) > 1:
        return Understanding(options=tuple(found))
    intent = found[0]
    if intent in ("home", "away") and _NEGATION.search(sentence):
        return Understanding(options=("home", "away"))
    return Understanding(command=" ".join([intent, *_arguments(intent, sentence, cameras)]))


def _arguments(intent: str, sentence: str, cameras: dict[str, str]) -> list[str]:
    args = []
    if intent in ("photo", "video", "sharper"):
        for camera_id, name in cameras.items():
            if normalize(name) in sentence or camera_id.replace("_", " ") in sentence:
                args.append(camera_id)
                break
    if intent == "video":
        if _MINUTE.search(sentence):
            args.append("60")
        elif match := _SECONDS.search(sentence):
            seconds = int(match.group(1))
            if 3 <= seconds <= 120:
                args.append(str(seconds))
    return args
