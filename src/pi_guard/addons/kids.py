"""Kid Protect: filter profiles for children's devices, with times without internet.

A profile has a name, the kinds of sites to block (adult content, gambling, malware and
scams, drugs, piracy, social networks, TikTok) and times without internet, such as 22:00
to 06:00 on school nights. Devices are given a profile, or none: then nothing changes for
them. Parents can pause a profile now, or give extra time while a time without internet
is on.

How it works: each profile is a group in Pi-hole, its devices (by MAC address) are clients
in that group as well as in the default group, and the block lists of its kinds are
assigned to the group only, so other devices are not filtered by them. A time without
internet adds a "block everything" rule for the group; if a FRITZ!Box is connected, the
router also cuts the device's internet, which works for devices that ask another DNS
server too. Only what this add-on switched off is switched on again.

DNS filtering is not a lock: a device with its own DNS server, a VPN or mobile data goes
around it. It is meant for younger children, together with talking to them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, time, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from pi_guard.core.timeutil import utcnow
from pi_guard.network.pihole import Pihole, PiholeError
from pi_guard.storage.db import Database

log = logging.getLogger(__name__)

SWEEP_SECONDS = 60
LIST = "https://blocklistproject.github.io/Lists/{}.txt"
# The Block List Project (blocklistproject.github.io), hosts format, and StevenBlack's
# social list. Pinned here; Pi-hole downloads them.
CATEGORIES: dict[str, tuple[str, ...]] = {
    "adult": (LIST.format("porn"),),
    "gambling": (LIST.format("gambling"),),
    "malware": (LIST.format("malware"), LIST.format("phishing"), LIST.format("ransomware")),
    "scam": (LIST.format("scam"), LIST.format("fraud")),
    "drugs": (LIST.format("drugs"),),
    "piracy": (LIST.format("piracy"), LIST.format("torrent")),
    "social": ("https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/social-only/hosts",),
    "tiktok": (LIST.format("tiktok"),),
}  # fmt: skip
DEFAULT_CATEGORIES = ("adult", "gambling", "malware", "scam", "drugs")
COMMENT = "pi-guard kids"
EVERYTHING = r".*"  # the regex of a time without internet
MAX_PROFILES = 20
MAX_WINDOWS = 14
MAX_MINUTES = 24 * 60
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_MAC = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
_TABLES = """
CREATE TABLE IF NOT EXISTS kid_profiles (
    id INTEGER PRIMARY KEY, data TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kid_devices (
    mac TEXT PRIMARY KEY, profile INTEGER NOT NULL REFERENCES kid_profiles(id) ON DELETE CASCADE,
    name TEXT NOT NULL DEFAULT '');
"""
CUT_KEY = "kids.cut"  # IPs whose internet this add-on cut at the router


class KidsError(ValueError):
    """`key` is a translation key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def clean(data: dict[str, Any]) -> dict[str, Any]:
    name = " ".join(str(data.get("name") or "").split())[:40]
    if not name:
        raise KidsError("web.kp_needs_name")
    categories = [c for c in CATEGORIES if c in (data.get("categories") or [])]
    windows = []
    for raw in (data.get("offline") or [])[:MAX_WINDOWS]:
        if not isinstance(raw, dict):
            continue
        start, end = str(raw.get("from", "")), str(raw.get("to", ""))
        days = sorted({int(d) for d in raw.get("days") or [] if str(d).isdigit() and int(d) < 7})
        if not (_TIME.match(start) and _TIME.match(end)) or start == end or not days:
            raise KidsError("web.kp_bad_time")
        windows.append({"from": start, "to": end, "days": days})
    timed = {k: data.get(k) for k in ("paused_until", "extra_until")}
    return {"name": name, "categories": categories, "offline": windows, **timed}


def _clock(text: str) -> time:
    hours, minutes = text.split(":")
    return time(int(hours), int(minutes))


def in_window(windows: list[dict[str, Any]], now: datetime) -> bool:
    """Whether `now` (local time) is inside a time without internet. A window that goes past
    midnight belongs to the day it starts: Sunday 22:00-06:00 ends on Monday morning."""
    today, clock = now.weekday(), now.time()
    for w in windows:
        start, end = _clock(w["from"]), _clock(w["to"])
        if start < end:
            if today in w["days"] and start <= clock < end:
                return True
        elif (today in w["days"] and clock >= start) or ((today - 1) % 7 in w["days"]
                                                          and clock < end):  # fmt: skip
            return True
    return False


def _after(value: Any, now: datetime) -> bool:
    try:
        return bool(value) and datetime.fromisoformat(str(value)) > now
    except ValueError:
        return False


class Kids:
    def __init__(self, db: Database, pihole: Pihole | None, fritz: Any, tz: ZoneInfo) -> None:
        self._db = db
        self._pihole = pihole
        self._fritz = fritz
        self._tz = tz
        self._lock = asyncio.Lock()
        self._offline: dict[int, bool] = {}
        db.executescript(_TABLES)

    @property
    def filters(self) -> bool:
        return self._pihole is not None

    @property
    def router(self) -> bool:
        return self._fritz is not None

    # --- profiles and devices -------------------------------------------------------------

    def profiles(self) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT id, data FROM kid_profiles ORDER BY id").fetchall()
        devices = self.devices()
        now = utcnow()
        return [{**json.loads(r["data"]), "id": r["id"],
                 "devices": [d for d in devices if d["profile"] == r["id"]],
                 "offline_now": self.offline(r["id"], json.loads(r["data"]), now)}
                for r in rows]  # fmt: skip

    def _profile(self, profile_id: int) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT data FROM kid_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        return json.loads(row["data"]) if row else None

    def save(self, profile_id: int | None, data: dict[str, Any]) -> int:
        old = self._profile(profile_id) if profile_id else {}
        if profile_id and old is None:
            raise KidsError("web.kp_not_found")
        values = clean({**(old or {}), **data})
        if profile_id is None:
            count = self._db.execute("SELECT COUNT(*) FROM kid_profiles").fetchone()[0]
            if count >= MAX_PROFILES:
                raise KidsError("web.kp_too_many")
            cursor = self._db.execute("INSERT INTO kid_profiles (data, created) VALUES (?, ?)",
                                      (json.dumps(values), utcnow().isoformat()))  # fmt: skip
            return int(cursor.lastrowid or 0)
        self._db.execute("UPDATE kid_profiles SET data = ? WHERE id = ?",
                         (json.dumps(values), profile_id))  # fmt: skip
        return profile_id

    def delete(self, profile_id: int) -> bool:
        self._db.execute("DELETE FROM kid_devices WHERE profile = ?", (profile_id,))
        return self._db.execute("DELETE FROM kid_profiles WHERE id = ?", (profile_id,)).rowcount > 0

    def devices(self) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT mac, profile, name FROM kid_devices ORDER BY name, mac")
        return [dict(r) for r in rows]

    def assign(self, mac: str, profile_id: int | None, name: str = "") -> None:
        mac = mac.strip().lower()
        if not _MAC.match(mac):
            raise KidsError("web.kp_bad_device")
        if profile_id is None:
            self._db.execute("DELETE FROM kid_devices WHERE mac = ?", (mac,))
            return
        if self._profile(profile_id) is None:
            raise KidsError("web.kp_not_found")
        self._db.execute("INSERT OR REPLACE INTO kid_devices (mac, profile, name) VALUES (?, ?, ?)",
                         (mac, profile_id, " ".join(name.split())[:40]))  # fmt: skip

    def pause(self, profile_id: int, minutes: int) -> None:
        """Without internet now, for `minutes`; 0 ends a pause."""
        self._timed(profile_id, "paused_until", minutes)

    def extra(self, profile_id: int, minutes: int) -> None:
        """Internet now despite a time without it, for `minutes`; 0 ends the extra time."""
        self._timed(profile_id, "extra_until", minutes)

    def _timed(self, profile_id: int, key: str, minutes: int) -> None:
        if not 0 <= minutes <= MAX_MINUTES:
            raise KidsError("web.kp_bad_minutes")
        until = (utcnow() + timedelta(minutes=minutes)).isoformat() if minutes else None
        other = "extra_until" if key == "paused_until" else "paused_until"
        self.save(profile_id, {key: until, **({other: None} if minutes else {})})

    def offline(self, profile_id: int, profile: dict[str, Any], now: datetime) -> bool:
        if _after(profile.get("paused_until"), now):
            return True
        if _after(profile.get("extra_until"), now):
            return False
        return in_window(profile.get("offline", []), now.astimezone(self._tz))

    # --- Pi-hole ----------------------------------------------------------------------------

    async def _groups(self) -> dict[str, int]:
        assert self._pihole is not None
        data = await self._pihole.request("GET", "/api/groups")
        return {str(g["name"]): int(g["id"]) for g in data.get("groups", [])}

    async def _group(self, profile_id: int, known: dict[str, int]) -> int:
        assert self._pihole is not None
        name = f"pi-guard-kids-{profile_id}"
        if name not in known:
            await self._pihole.request("POST", "/api/groups", json={
                "name": name, "comment": COMMENT, "enabled": True})  # fmt: skip
            known.update(await self._groups())
        return known[name]

    async def sync(self) -> dict[str, Any]:
        """Bring Pi-hole in line with the profiles: groups, clients, lists, and the rule of a
        time without internet. Returns whether the block lists changed (gravity is needed)."""
        if self._pihole is None:
            return {"lists_changed": False}
        async with self._lock:
            return await self._sync()

    async def _sync(self) -> dict[str, Any]:
        pihole = self._pihole
        assert pihole is not None
        profiles = self.profiles()
        known = await self._groups()
        group_of = {p["id"]: await self._group(p["id"], known) for p in profiles}
        ours = {g for n, g in known.items() if n.startswith("pi-guard-kids-")}
        # Clients: a device in a profile is in its group and in the default group.
        clients = {str(c.get("client", "")).lower(): c for c in
                   (await pihole.request("GET", "/api/clients")).get("clients", [])}  # fmt: skip
        wanted_clients = {d["mac"]: group_of[d["profile"]] for d in self.devices()}
        for mac, client in clients.items():
            if mac in wanted_clients or not ours & set(client.get("groups", [])):
                continue
            groups = [g for g in client.get("groups", []) if g not in ours] or [0]
            await pihole.request("PUT", f"/api/clients/{quote(mac, safe='')}", json={
                "comment": client.get("comment") or "", "groups": groups})  # fmt: skip
        for mac, gid in wanted_clients.items():
            client = clients.get(mac)
            others = [g for g in (client or {}).get("groups", [0]) if g not in ours]
            groups = sorted({0, gid, *others})
            if client is None:
                await pihole.request("POST", "/api/clients", json={
                    "client": mac, "comment": COMMENT, "groups": groups})  # fmt: skip
            elif sorted(client.get("groups", [])) != groups:
                await pihole.request("PUT", f"/api/clients/{quote(mac, safe='')}", json={
                    "comment": client.get("comment") or COMMENT, "groups": groups})  # fmt: skip
        # Lists: each assigned to the groups of the profiles that use its kind.
        listed = await pihole.request("GET", "/api/lists", params={"type": "block"})
        lists = {x["address"]: x for x in listed.get("lists", [])}
        changed = False
        for address in {a for urls in CATEGORIES.values() for a in urls}:
            users = {group_of[p["id"]] for p in profiles
                     if any(address in CATEGORIES[c] for c in p["categories"])}  # fmt: skip
            found = lists.get(address)
            kept = {g for g in (found or {}).get("groups", []) if g not in ours}
            groups = sorted(kept | users)
            if found is None and users:
                body = {"address": address, "comment": COMMENT, "groups": groups, "enabled": True}
                await pihole.request("POST", "/api/lists", params={"type": "block"}, json=body)
                changed = True
            elif found is not None and not groups and found.get("comment") == COMMENT:
                await pihole.request("DELETE", f"/api/lists/{quote(address, safe='')}",
                                     params={"type": "block"})  # fmt: skip
                changed = True
            elif found is not None and sorted(found.get("groups", [])) != groups:
                await pihole.request("PUT", f"/api/lists/{quote(address, safe='')}",
                                     params={"type": "block"}, json={
                    "comment": found.get("comment") or COMMENT, "groups": groups or [0],
                    "enabled": bool(found.get("enabled", True)), "type": "block"})  # fmt: skip
                changed = True
        await self._everything(pihole, {group_of[p["id"]] for p in profiles if p["offline_now"]})
        for name in list(known):  # groups of profiles that are gone, now that nothing uses them
            if name.startswith("pi-guard-kids-") and int(name.rsplit("-", 1)[1]) not in group_of:
                await pihole.request("DELETE", f"/api/groups/{quote(name, safe='')}")
        return {"lists_changed": changed}

    async def _everything(self, pihole: Pihole, groups: set[int]) -> None:
        """The rule of a time without internet, for the groups that are offline now."""
        path = f"/api/domains/deny/regex/{quote(EVERYTHING, safe='')}"
        found = (await pihole.request("GET", path)).get("domains", [])
        if not found:
            if groups:
                await pihole.request("POST", "/api/domains/deny/regex", json={
                    "domain": EVERYTHING, "comment": COMMENT, "groups": sorted(groups),
                    "enabled": True})  # fmt: skip
            return
        entry = found[0]
        if entry.get("comment") != COMMENT:
            log.warning("a block-everything rule exists in Pi-hole that is not ours; left alone")
            return
        enabled = bool(groups)
        wanted = sorted(groups) or [0]
        if bool(entry.get("enabled")) != enabled or sorted(entry.get("groups", [])) != wanted:
            await pihole.request("PUT", path, json={
                "comment": COMMENT, "groups": wanted, "enabled": enabled})  # fmt: skip

    # --- the router -------------------------------------------------------------------------

    async def _router(self, offline_ips: set[str]) -> None:
        """Cuts the internet of offline devices at the FRITZ!Box; gives back only what it cut."""
        if self._fritz is None:
            return
        cut = set(self._db.get(CUT_KEY) or [])
        for ip in sorted(offline_ips - cut):
            await self._fritz.set_internet(ip, False)
            cut.add(ip)
        for ip in sorted(cut - offline_ips):
            await self._fritz.set_internet(ip, True)
            cut.discard(ip)
        self._db.set(CUT_KEY, sorted(cut))

    async def _addresses(self) -> dict[str, str]:
        """MAC -> IPv4 of the devices, from the router or Pi-hole."""
        found: dict[str, str] = {}
        if self._fritz is not None:
            for host in await self._fritz.hosts():
                if host.ip and ":" not in host.ip:
                    found[host.mac.lower()] = host.ip
        elif self._pihole is not None:
            for device in await self._pihole.devices():
                ip = next((i for i in device.ips if ":" not in i), None)
                if ip and device.mac:
                    found[device.mac.lower()] = ip
        return found

    async def apply(self) -> None:
        """One sweep: filters and the times without internet as they should be now."""
        now = utcnow()
        profiles = self.profiles()
        state = {p["id"]: p["offline_now"] for p in profiles}
        changed = state != self._offline
        if self._pihole is not None and changed:
            result = await self.sync()
            if result["lists_changed"]:
                await self._pihole.update_gravity()
        if self._fritz is not None:
            offline = {d["mac"] for p in profiles if self.offline(p["id"], p, now)
                       for d in p["devices"]}  # fmt: skip
            addresses = await self._addresses() if offline or self._db.get(CUT_KEY) else {}
            await self._router({addresses[m] for m in offline if m in addresses})
        self._offline = state

    async def run(self) -> None:
        while True:
            try:
                await self.apply()
            except (PiholeError, OSError, RuntimeError) as exc:
                log.warning("kid protect could not apply its profiles: %s", exc)
                self._offline = {}  # try the whole sync again next time
            except Exception as exc:  # the router answers in many ways
                log.warning("kid protect: %s", exc)
            await asyncio.sleep(SWEEP_SECONDS)
