"""ALSA device discovery from /proc/asound (no alsa-utils needed).

Devices are addressed by card *name* (`plughw:CARD=Device,DEV=0`), which stays
stable when USB devices are plugged in a different order, unlike card numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_CARD_RE = re.compile(r"^\s*(\d+)\s+\[(\S+)\s*\]:\s*(.+?)\s*$")
_PCM_RE = re.compile(r"^(\d+)-(\d+):\s*([^:]*):\s*([^:]*):\s*(.*)$")


@dataclass(frozen=True)
class AudioDevice:
    card: int
    card_id: str
    device: int
    name: str
    playback: bool
    capture: bool

    @property
    def alsa_name(self) -> str:
        return f"plughw:CARD={self.card_id},DEV={self.device}"

    @property
    def usb(self) -> bool:
        return "usb" in self.name.lower()


def parse(cards_text: str, pcm_text: str) -> list[AudioDevice]:
    cards: dict[int, tuple[str, str]] = {}
    for line in cards_text.splitlines():
        match = _CARD_RE.match(line)
        if match:
            cards[int(match.group(1))] = (match.group(2), match.group(3))
    devices = []
    for line in pcm_text.splitlines():
        match = _PCM_RE.match(line.strip())
        if not match:
            continue
        card, device = int(match.group(1)), int(match.group(2))
        card_id, card_name = cards.get(card, (str(card), "unknown"))
        caps = match.group(5)
        devices.append(
            AudioDevice(
                card,
                card_id,
                device,
                f"{card_name} – {match.group(3).strip()}",
                playback="playback" in caps,
                capture="capture" in caps,
            )
        )
    return devices


def discover(root: Path = Path("/proc/asound")) -> list[AudioDevice]:
    try:
        return parse((root / "cards").read_text(), (root / "pcm").read_text())
    except OSError:
        return []
