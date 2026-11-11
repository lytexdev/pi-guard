"""Kid Protect: profiles in Pi-hole groups, times without internet, the router's cut."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import pytest

from pi_guard.addons import kids as kids_module
from pi_guard.addons.kids import CATEGORIES, EVERYTHING, Kids, KidsError, clean, in_window
from pi_guard.storage.db import Database

TZ = ZoneInfo("Europe/Berlin")
NIGHT = {"from": "22:00", "to": "06:00", "days": [0, 1, 2, 3, 6]}  # Sunday to Thursday evenings


class FakePihole:
    """Enough of Pi-hole's API: groups, clients, lists and regex rules."""

    def __init__(self) -> None:
        self.groups: dict[str, int] = {"Default": 0}
        self.clients: dict[str, dict[str, Any]] = {}
        self.lists: dict[str, dict[str, Any]] = {}
        self.regex: dict[str, dict[str, Any]] = {}
        self.gravity = 0

    async def request(self, method: str, path: str, **kw: Any) -> Any:
        body = kw.get("json") or {}
        key = unquote(path.rsplit("/", 1)[-1])
        if path == "/api/groups":
            if method == "POST":
                self.groups[body["name"]] = max(self.groups.values()) + 1
            return {"groups": [{"name": n, "id": i} for n, i in self.groups.items()]}
        if path.startswith("/api/groups/") and method == "DELETE":
            self.groups.pop(key)
            return {}
        if path == "/api/clients":
            if method == "POST":
                self.clients[body["client"]] = {"client": body["client"], **body}
            return {"clients": list(self.clients.values())}
        if path.startswith("/api/clients/"):
            self.clients[key] |= body
            return {}
        if path == "/api/lists":
            if method == "POST":
                self.lists[body["address"]] = dict(body)
            return {"lists": list(self.lists.values())}
        if path.startswith("/api/lists/"):
            if method == "DELETE":
                self.lists.pop(key)
            else:
                self.lists[key] |= body
            return {}
        if path == "/api/domains/deny/regex":
            self.regex[body["domain"]] = dict(body)
            return {}
        if path.startswith("/api/domains/deny/regex/"):
            if method == "GET":
                return {"domains": [self.regex[key]] if key in self.regex else []}
            self.regex[key] |= body
            return {}
        raise AssertionError(f"unexpected {method} {path}")

    async def update_gravity(self) -> bool:
        self.gravity += 1
        return True

    async def devices(self) -> list[Any]:
        return []


class FakeRouter:
    def __init__(self) -> None:
        self.blocked: set[str] = {"192.168.1.99"}  # blocked by hand, not by the add-on

    async def hosts(self) -> list[Any]:
        from pi_guard.network.fritzbox import FritzHost

        return [FritzHost("aa:bb:cc:dd:ee:01", "192.168.1.20", "tablet", True, "802.11", 0)]

    async def set_internet(self, ip: str, allowed: bool) -> None:
        (self.blocked.discard if allowed else self.blocked.add)(ip)


@pytest.fixture
def db(tmp_path: Path):  # type: ignore[no-untyped-def]
    database = Database(tmp_path / "pi-guard.db")
    yield database
    database.close()


def test_times_without_internet_cross_midnight() -> None:
    sunday_night = datetime(2026, 11, 8, 23, 30, tzinfo=TZ)
    assert sunday_night.weekday() == 6 and in_window([NIGHT], sunday_night)
    assert in_window([NIGHT], sunday_night + timedelta(hours=6))  # Monday 05:30
    assert not in_window([NIGHT], sunday_night + timedelta(hours=7))  # Monday 06:30
    friday_night = datetime(2026, 11, 13, 23, 0, tzinfo=TZ)
    assert not in_window([NIGHT], friday_night)  # Friday is not in the days
    assert not in_window([NIGHT], friday_night + timedelta(hours=5))  # Saturday morning neither
    homework = {"from": "14:00", "to": "16:00", "days": [2]}
    assert in_window([homework], datetime(2026, 11, 11, 15, 0, tzinfo=TZ))
    assert not in_window([homework], datetime(2026, 11, 11, 16, 0, tzinfo=TZ))


def test_profiles_are_checked() -> None:
    made = clean({"name": " Mia ", "categories": ["adult", "nonsense"], "offline": [NIGHT]})
    assert made["name"] == "Mia" and made["categories"] == ["adult"]
    for bad in ({"from": "22:00", "to": "22:00", "days": [1]}, {"from": "25:00", "to": "06:00",
                "days": [1]}, {"from": "22:00", "to": "06:00", "days": []}):  # fmt: skip
        with pytest.raises(KidsError):
            clean({"name": "A", "offline": [bad]})
    with pytest.raises(KidsError, match="name"):
        clean({"categories": ["adult"]})


async def test_a_profile_filters_only_its_devices(db: Database, monkeypatch: Any) -> None:
    hole = FakePihole()
    malware = CATEGORIES["malware"][0]  # already a list for everyone, added by hand
    hole.lists[malware] = {"address": malware, "comment": "mine", "groups": [0], "enabled": True}
    hole.clients["aa:bb:cc:dd:ee:09"] = {"client": "aa:bb:cc:dd:ee:09", "groups": [0, 5]}
    kids = Kids(db, hole, None, TZ)  # type: ignore[arg-type]
    mia = kids.save(None, {"name": "Mia", "categories": ["adult", "malware"]})
    kids.assign("AA:BB:CC:DD:EE:01", mia, "Tablet")
    with pytest.raises(KidsError):
        kids.assign("not a mac", mia)
    assert (await kids.sync())["lists_changed"]
    group = hole.groups[f"pi-guard-kids-{mia}"]
    assert hole.clients["aa:bb:cc:dd:ee:01"]["groups"] == [0, group]
    assert hole.clients["aa:bb:cc:dd:ee:09"]["groups"] == [0, 5]  # someone else's, untouched
    assert hole.lists[CATEGORIES["adult"][0]]["groups"] == [group]  # not for everyone
    assert hole.lists[CATEGORIES["malware"][0]]["groups"] == [0, group]  # kept for everyone
    assert CATEGORIES["gambling"][0] not in hole.lists
    assert not (await kids.sync())["lists_changed"]  # nothing to do the second time

    kids.save(mia, {"categories": []})
    await kids.sync()
    assert CATEGORIES["adult"][0] not in hole.lists  # ours, unused: gone
    assert hole.lists[CATEGORIES["malware"][0]]["groups"] == [0]  # theirs: back as it was

    kids.pause(mia, 30)
    await kids.sync()
    rule = hole.regex[EVERYTHING]
    assert rule["enabled"] and rule["groups"] == [group]
    kids.extra(mia, 30)  # extra time ends the pause
    await kids.sync()
    assert not hole.regex[EVERYTHING]["enabled"]

    assert kids.delete(mia)
    await kids.sync()
    assert f"pi-guard-kids-{mia}" not in hole.groups
    assert hole.clients["aa:bb:cc:dd:ee:01"]["groups"] == [0]


async def test_the_router_cuts_and_gives_back_only_its_own(db: Database, monkeypatch: Any) -> None:
    router = FakeRouter()
    kids = Kids(db, None, router, TZ)
    mia = kids.save(None, {"name": "Mia", "offline": [NIGHT]})
    kids.assign("aa:bb:cc:dd:ee:01", mia)
    late = datetime(2026, 11, 8, 23, 0, tzinfo=TZ)
    monkeypatch.setattr(kids_module, "utcnow", lambda: late)
    await kids.apply()
    assert router.blocked == {"192.168.1.20", "192.168.1.99"}
    monkeypatch.setattr(kids_module, "utcnow", lambda: late + timedelta(hours=8))
    await kids.apply()
    assert router.blocked == {"192.168.1.99"}  # the one blocked by hand stays blocked
    with pytest.raises(KidsError, match="minutes"):
        kids.pause(mia, -1)


async def test_parents_set_it_up_in_the_dashboard(tmp_path: Path) -> None:
    from .test_ecosystem import enable
    from .test_web import _client, _login

    client, app = await _client(tmp_path)
    try:
        headers = {"X-CSRF-Token": await _login(client)}
        assert (await client.get("/api/kids")).status == 409  # add-on off
        hole = FakePihole()
        app.pihole = hole
        enable(app, kids={"enabled": True})
        data = await (await client.get("/api/kids")).json()
        assert data["filters"] and not data["router"] and "adult" in data["defaults"]
        body = {"name": "Mia", "categories": ["adult"], "offline": [NIGHT]}
        made = await (await client.post("/api/kids/profiles", json=body, headers=headers)).json()
        assert made["problem"] is None and hole.gravity == 1
        device = {"mac": "AA:BB:CC:DD:EE:01", "name": "Tablet", "profile": made["id"]}
        assert (await client.put("/api/kids/devices", json=device, headers=headers)).status == 200
        assert "aa:bb:cc:dd:ee:01" in hole.clients
        paused = await client.post(f"/api/kids/profiles/{made['id']}/pause", json={"minutes": 30},
                                   headers=headers)  # fmt: skip
        assert paused.status == 200 and hole.regex[EVERYTHING]["enabled"]
        bad = await client.post(f"/api/kids/profiles/{made['id']}/extra", json={"minutes": "x"},
                                headers=headers)  # fmt: skip
        assert bad.status == 400
        profiles = (await (await client.get("/api/kids")).json())["profiles"]
        assert profiles[0]["offline_now"] and profiles[0]["devices"][0]["name"] == "Tablet"
        actions = {r[0] for r in app.db.execute("SELECT action FROM audit")}
        assert {"kids.save", "kids.device", "kids.pause"} <= actions
    finally:
        await client.close()
        app.db.close()
