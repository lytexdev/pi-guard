"""Read recent sections from CHANGELOG.md for the Updates page."""

from __future__ import annotations

import re
from pathlib import Path

# Installed tree first, then the development checkout next to the package.
_CANDIDATES = (
    Path("/opt/pi-guard/CHANGELOG.md"),
    Path(__file__).resolve().parents[3] / "CHANGELOG.md",
)

_HEADING = re.compile(r"^##\s+\[([^\]]+)\](.*)$")


def changelog_path() -> Path | None:
    for path in _CANDIDATES:
        if path.is_file():
            return path
    return None


def recent(limit: int = 3) -> list[dict[str, str]]:
    """Newest version blocks (title + markdown body), Unreleased first when present."""
    path = changelog_path()
    if path is None:
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    sections: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    body: list[str] = []
    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            if current is not None:
                sections.append({**current, "body": "\n".join(body).strip()})
            current = {"version": match.group(1).strip(), "title": line[3:].strip()}
            body = []
            continue
        if current is not None:
            body.append(line)
    if current is not None:
        sections.append({**current, "body": "\n".join(body).strip()})
    return [s for s in sections if s["body"]][: max(1, limit)]
