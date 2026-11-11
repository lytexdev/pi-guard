"""Phones with the pi-guard app: pairing, live connection, finding them, emergencies.

Platform-neutral on purpose: an iOS app would pair and talk the same way. Phones connect out to
pi-guard over a WebSocket, so alerts need neither Google nor Apple. A device token only opens
what the app needs; it is stored as a hash and can be revoked here.
"""

from __future__ import annotations

import base64
import builtins
import hashlib
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from pi_guard.core.timeutil import utcnow
from pi_guard.messaging.base import MessageHandler, ProviderUnavailableError, Reply
from pi_guard.storage.db import Database

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mobile_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, platform TEXT NOT NULL,
    username TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE, created TEXT NOT NULL, last_seen TEXT, info TEXT,
    vpn_peer INTEGER);
CREATE TABLE IF NOT EXISTS mobile_track (
    device_id INTEGER NOT NULL, ts TEXT NOT NULL, latitude REAL NOT NULL, longitude REAL NOT NULL,
    accuracy REAL);
CREATE INDEX IF NOT EXISTS mobile_track_device ON mobile_track (device_id, ts);
"""
# While a phone is lost or after its SOS, its places are kept as a track, for this long at most.
TRACK_DAYS = 7
EMERGENCY_MINUTES = 30
LOST_EVERY = 60  # seconds between places of a lost phone
MAX_NAME = 40
MAX_PHOTO = 700_000  # a photo has to fit into one message
PLATFORMS = ("android", "ios", "linux", "windows", "macos")
REPORT_FIELDS = {"latitude": float, "longitude": float, "accuracy": float, "altitude": float,
                 "speed": float, "located_at": str, "battery": int, "charging": bool,
                 "network": str, "ssid": str, "ip": str, "model": str, "os": str, "app": str,
                 "vpn": bool, "usage_ok": bool}  # fmt: skip
Send = Callable[[str], Awaitable[None]]


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def clean_report(raw: Any) -> dict[str, Any]:
    """Only known fields of the right type: this text ends up in messages and on pages."""
    if not isinstance(raw, dict):
        return {}
    report: dict[str, Any] = {}
    for key, kind in REPORT_FIELDS.items():
        value = raw.get(key)
        if isinstance(value, bool) != (kind is bool):
            continue
        if kind is float and isinstance(value, (int, float)):
            report[key] = float(value)
        elif kind is int and isinstance(value, int):
            report[key] = value
        elif kind is str and isinstance(value, str):
            report[key] = value[:80]
        elif kind is bool and isinstance(value, bool):
            report[key] = value
    if not (-90 <= report.get("latitude", 0) <= 90 and -180 <= report.get("longitude", 0) <= 180):
        report.pop("latitude", None)
        report.pop("longitude", None)
    return report


# Where a place opens. "geo" is the map app of the device that opens the link.
MAP_PROVIDERS = {
    "openstreetmap": "https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=18/{lat}/{lon}",
    "google": "https://www.google.com/maps/search/?api=1&query={lat},{lon}",
    "apple": "https://maps.apple.com/?ll={lat},{lon}&q={lat},{lon}&z=18",
    "bing": "https://www.bing.com/maps?cp={lat}~{lon}&lvl=18&sp=point.{lat}_{lon}_",
    "here": "https://wego.here.com/?map={lat},{lon},18",
    "geo": "geo:{lat},{lon}?q={lat},{lon}",
}


def map_link(report: dict[str, Any], provider: str = "openstreetmap") -> str | None:
    if "latitude" not in report or "longitude" not in report:
        return None
    pattern = MAP_PROVIDERS.get(provider, MAP_PROVIDERS["openstreetmap"])
    # Six decimals are about 10 cm: as exact as any phone can be.
    return pattern.format(lat=f"{report['latitude']:.6f}", lon=f"{report['longitude']:.6f}")


class MobileHub:
    def __init__(self, db: Database) -> None:
        self._db = db
        db.executescript(_SCHEMA)
        self._sockets: dict[int, Send] = {}

    def create(self, name: str, platform: str, username: str) -> tuple[int, str]:
        name = " ".join(name.split())[:MAX_NAME] or "Phone"
        if platform not in PLATFORMS:
            raise ValueError("platform")
        token = secrets.token_urlsafe(32)
        cursor = self._db.execute(
            "INSERT INTO mobile_devices (name, platform, username, token, created) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, platform, username, _digest(token), utcnow().isoformat()),
        )
        return int(cursor.lastrowid or 0), token

    def delete(self, device_id: int) -> bool:
        self._sockets.pop(device_id, None)
        self.stop(device_id)
        return self._db.execute("DELETE FROM mobile_devices WHERE id = ?",
                                (device_id,)).rowcount > 0  # fmt: skip

    def authenticate(self, token: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM mobile_devices WHERE token = ?",
                               (_digest(token),)).fetchone()  # fmt: skip
        return self._row(row) if row else None

    def _row(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        data.pop("token", None)
        data["info"] = json.loads(data["info"] or "{}")
        data["online"] = data["id"] in self._sockets
        return data

    def of_user(self, username: str) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT * FROM mobile_devices WHERE username = ?",
                                (username,)).fetchall()  # fmt: skip
        return [self._row(r) for r in rows]

    def list(self) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT * FROM mobile_devices ORDER BY name").fetchall()
        return [self._row(r) for r in rows]

    def get(self, device_id: int) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM mobile_devices WHERE id = ?",
                               (device_id,)).fetchone()  # fmt: skip
        return self._row(row) if row else None

    def find(self, text: str) -> dict[str, Any] | None:
        wanted = text.strip().casefold()
        devices = self.list()
        if not wanted and len(devices) == 1:
            return devices[0]
        return next((d for d in devices if wanted and wanted in d["name"].casefold()), None)

    def report(self, device_id: int, raw: Any) -> dict[str, Any]:
        device = self.get(device_id) or {"info": {}}
        fresh = clean_report(raw)
        info = {**device["info"], **fresh}
        self._db.execute("UPDATE mobile_devices SET info = ?, last_seen = ? WHERE id = ?",
                         (json.dumps(info), utcnow().isoformat(), device_id))  # fmt: skip
        if "latitude" in fresh and "longitude" in fresh and self.tracking(device_id):
            # The Pi's own time: the phone's clock and text are not trusted for the order.
            self._db.execute("INSERT INTO mobile_track VALUES (?, ?, ?, ?, ?)",
                             (device_id, utcnow().isoformat(), fresh["latitude"],
                              fresh["longitude"], fresh.get("accuracy")))  # fmt: skip
        return info

    # --- lost phones and emergencies: a track of places, and nothing longer than needed ---

    def _state(self) -> dict[str, Any]:
        return dict(self._db.get("mobile.tracking") or {})

    def lost(self, device_id: int) -> dict[str, Any] | None:
        entry = self._state().get(str(device_id))
        return entry if entry and entry.get("kind") == "lost" else None

    def tracking(self, device_id: int) -> bool:
        entry = self._state().get(str(device_id))
        if not entry:
            return False
        if entry.get("kind") == "emergency":
            started = datetime.fromisoformat(entry["since"])
            return utcnow() - started < timedelta(minutes=EMERGENCY_MINUTES)
        return True

    def start(self, device_id: int, kind: str, message: str = "", by: str = "") -> None:
        state = self._state()
        state[str(device_id)] = {"kind": kind, "since": utcnow().isoformat(),
                                 "message": message[:200], "by": by, "files": 0}  # fmt: skip
        self._db.set("mobile.tracking", state)

    def count_file(self, device_id: int, limit: int) -> bool:
        """One more file from the phone for this emergency, if it has not sent `limit` yet."""
        state = self._state()
        entry = state.get(str(device_id))
        if not entry or entry.get("files", 0) >= limit:
            return False
        entry["files"] = entry.get("files", 0) + 1
        self._db.set("mobile.tracking", state)
        return True

    def stop(self, device_id: int) -> None:
        """Found, or the emergency is over: the track is deleted with it."""
        state = self._state()
        state.pop(str(device_id), None)
        self._db.set("mobile.tracking", state)
        self._db.execute("DELETE FROM mobile_track WHERE device_id = ?", (device_id,))

    def track(self, device_id: int, limit: int = 500) -> builtins.list[dict[str, Any]]:
        cutoff = (utcnow() - timedelta(days=TRACK_DAYS)).isoformat()
        self._db.execute("DELETE FROM mobile_track WHERE ts < ?", (cutoff,))
        rows = self._db.execute(
            "SELECT ts, latitude, longitude, accuracy FROM mobile_track WHERE device_id = ? "
            "ORDER BY ts DESC LIMIT ?", (device_id, limit)).fetchall()  # fmt: skip
        return [{"time": r[0], "latitude": r[1], "longitude": r[2], "accuracy": r[3]} for r in rows]

    def states(self) -> dict[str, Any]:
        return {k: v for k, v in self._state().items() if self.tracking(int(k))}

    def set_vpn_peer(self, device_id: int, peer_id: int) -> None:
        self._db.execute("UPDATE mobile_devices SET vpn_peer = ? WHERE id = ?",
                         (peer_id, device_id))  # fmt: skip

    def attach(self, device_id: int, send: Send) -> None:
        self._sockets[device_id] = send

    def detach(self, device_id: int, send: Send) -> None:
        if self._sockets.get(device_id) is send:
            del self._sockets[device_id]

    async def send(self, device_id: int, **message: Any) -> None:
        send = self._sockets.get(device_id)
        if send is None:
            raise ProviderUnavailableError("device offline")
        await send(json.dumps(message))


class MobileProvider:
    """Alerts reach phones like any other messenger; commands stay in the app."""

    name = "mobile"
    supports_buttons = False

    def __init__(self, hub: MobileHub) -> None:
        self._hub = hub

    @property
    def connected(self) -> bool:
        return True

    async def run(self, on_message: MessageHandler) -> None:
        import asyncio

        await asyncio.Event().wait()

    async def send(self, chat_id: str, reply: Reply) -> None:
        message: dict[str, Any] = {"type": "alert", "text": reply.text}
        if reply.photo is not None and reply.photo.stat().st_size <= MAX_PHOTO:
            message["photo"] = base64.b64encode(reply.photo.read_bytes()).decode()
        await self._hub.send(int(chat_id), **message)
