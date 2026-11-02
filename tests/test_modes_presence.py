from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pi_guard.config.files import PresenceSettings, TrustedDevice
from pi_guard.core.events import EventBus, EventType
from pi_guard.core.modes import AutoUnavailableError, Mode, ModeController, Presence, Selection
from pi_guard.presence.monitor import PresenceMonitor
from pi_guard.presence.resolver import Observation, PresenceResolver, combine
from pi_guard.presence.sources import parse_host_entry, parse_neighbours
from pi_guard.storage.db import Database

T0 = datetime(2026, 10, 30, 8, 0, tzinfo=UTC)
SEEN, MISS, ERR = Observation.SEEN, Observation.NOT_SEEN, Observation.ERROR


def controller(
    db: Database, presence: bool = True, unknown: str = "AWAY", bus: EventBus | None = None
) -> ModeController:
    return ModeController(db, bus, Mode.HOME, presence, unknown)  # type: ignore[arg-type]


# --- mode controller ---------------------------------------------------------------------


def test_presence_drives_mode_in_auto(db: Database) -> None:
    modes = controller(db)
    assert modes.state.selection is Selection.AUTO
    modes.update_presence(Presence.AWAY)
    assert modes.effective is Mode.AWAY
    modes.update_presence(Presence.HOME)
    assert modes.effective is Mode.HOME


def test_manual_override_beats_presence(db: Database) -> None:
    modes = controller(db)
    modes.set_manual(Mode.AWAY)
    modes.update_presence(Presence.HOME)
    assert modes.effective is Mode.AWAY
    assert modes.state.selection is Selection.MANUAL_OVERRIDE
    modes.set_auto()
    assert modes.effective is Mode.HOME  # control handed back, presence applies


def test_unknown_presence_uses_configured_fallback(db: Database) -> None:
    away = controller(db, unknown="AWAY")
    away.update_presence(Presence.HOME)
    away.update_presence(Presence.UNKNOWN)
    assert away.effective is Mode.AWAY
    db2 = Database(db.path.with_name("other.db"))
    keep = controller(db2, unknown="last")
    keep.update_presence(Presence.HOME)
    keep.update_presence(Presence.UNKNOWN)
    assert keep.effective is Mode.HOME
    db2.close()


def test_timed_override_returns_to_auto(db: Database) -> None:
    modes = controller(db)
    modes.update_presence(Presence.HOME)
    modes.set_manual(Mode.AWAY, timedelta(hours=1))
    assert modes.effective is Mode.AWAY
    modes.tick(datetime.now(UTC) + timedelta(minutes=59))
    assert modes.effective is Mode.AWAY
    modes.tick(datetime.now(UTC) + timedelta(hours=1, seconds=1))
    assert modes.effective is Mode.HOME
    assert modes.state.selection is Selection.AUTO


def test_timed_override_without_presence_reverts(db: Database) -> None:
    modes = controller(db, presence=False)
    modes.set_manual(Mode.AWAY)
    modes.set_manual(Mode.HOME, timedelta(minutes=30))
    modes.tick(datetime.now(UTC) + timedelta(hours=1))
    assert modes.effective is Mode.AWAY
    assert modes.state.manual_until is None


def test_auto_unavailable_without_presence(db: Database) -> None:
    with pytest.raises(AutoUnavailableError):
        controller(db, presence=False).set_auto()


def test_mode_persists_across_restart(db: Database) -> None:
    controller(db, presence=False).set_manual(Mode.AWAY)
    restored = controller(db, presence=False)
    assert restored.effective is Mode.AWAY


def test_auto_selection_downgraded_when_presence_disabled(db: Database) -> None:
    controller(db, presence=True).update_presence(Presence.AWAY)
    restored = controller(db, presence=False)
    assert restored.state.selection is Selection.MANUAL_OVERRIDE
    assert restored.effective is Mode.AWAY


def test_change_from_cli_is_picked_up(db: Database) -> None:
    bus = EventBus()
    events = bus.subscribe("t")
    service = controller(db, presence=False, bus=bus)
    cli = controller(db, presence=False)
    cli.set_manual(Mode.AWAY, actor="cli")
    service.tick()
    assert service.effective is Mode.AWAY
    assert events.get_nowait().type is EventType.MODE_CHANGED


def test_mode_change_event_contains_actor(db: Database) -> None:
    bus = EventBus()
    events = bus.subscribe("t")
    controller(db, presence=False, bus=bus).set_manual(Mode.AWAY, actor="telegram:1")
    event = events.get_nowait()
    assert event.data == {
        "mode": "AWAY",
        "previous": "HOME",
        "selection": "MANUAL_OVERRIDE",
        "actor": "telegram:1",
    }


# --- presence resolver -------------------------------------------------------------------


def resolver(**kw: int) -> PresenceResolver:
    settings = PresenceSettings(
        home_confirmation_seconds=kw.get("confirm", 30),
        away_grace_period_seconds=kw.get("grace", 600),
    )
    return PresenceResolver(settings, started=T0)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def test_home_requires_confirmation() -> None:
    r = resolver()
    assert r.update(at(0), {"phone": SEEN}) is Presence.UNKNOWN
    assert r.update(at(30), {"phone": SEEN}) is Presence.HOME


def test_single_blip_does_not_confirm_home() -> None:
    r = resolver()
    r.update(at(0), {"phone": SEEN})
    r.update(at(30), {"phone": MISS})
    assert r.update(at(60), {"phone": SEEN}) is Presence.UNKNOWN


def test_sleeping_phone_stays_home_within_grace() -> None:
    r = resolver(grace=600)
    r.update(at(0), {"phone": SEEN})
    r.update(at(30), {"phone": SEEN})
    for t in range(60, 600, 30):
        assert r.update(at(t), {"phone": MISS}) is Presence.HOME
    assert r.update(at(631), {"phone": MISS}) is Presence.AWAY


def test_any_trusted_device_means_home() -> None:
    r = resolver()
    r.update(at(0), {"a": MISS, "b": SEEN})
    assert r.update(at(30), {"a": MISS, "b": SEEN}) is Presence.HOME


def test_failing_checks_lead_to_unknown_not_away() -> None:
    r = resolver(grace=600)
    r.update(at(0), {"phone": SEEN})
    r.update(at(30), {"phone": SEEN})
    for t in range(60, 600, 30):
        assert r.update(at(t), {"phone": ERR}) is Presence.HOME
    assert r.update(at(660), {"phone": ERR}) is Presence.UNKNOWN


def test_partial_errors_block_away() -> None:
    r = resolver(grace=60)
    r.state = Presence.HOME
    assert r.update(at(120), {"a": MISS, "b": ERR}) is Presence.HOME
    assert r.update(at(150), {"a": MISS, "b": MISS}) is Presence.AWAY


def test_away_needs_monitoring_for_grace_period() -> None:
    r = resolver(grace=600)
    assert r.update(at(30), {"phone": MISS}) is Presence.UNKNOWN
    assert r.update(at(600), {"phone": MISS}) is Presence.AWAY


def test_away_to_home_on_return() -> None:
    r = resolver(grace=60, confirm=0)
    r.update(at(60), {"phone": MISS})
    assert r.state is Presence.AWAY
    assert r.update(at(90), {"phone": SEEN}) is Presence.HOME


def test_combine_sources() -> None:
    assert combine([ERR, SEEN]) is SEEN
    assert combine([MISS, ERR]) is MISS
    assert combine([ERR, ERR]) is ERR
    assert combine([]) is ERR


def test_parse_neighbour_table() -> None:
    output = """[
      {"dst":"192.168.178.40","dev":"wlan0","lladdr":"58:D9:D5:A7:C8:45","state":["STALE"]},
      {"dst":"fe80::1","dev":"wlan0","lladdr":"58:d9:d5:a7:c8:45","state":["REACHABLE"]},
      {"dst":"192.168.178.9","dev":"wlan0","state":["FAILED"]}
    ]"""
    table = parse_neighbours(output)
    assert table == {"58:d9:d5:a7:c8:45": ("fe80::1", {"REACHABLE"})}


def test_parse_fritzbox_host_entry() -> None:
    xml = """<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
    <s:Body><u:GetSpecificHostEntryResponse xmlns:u="urn:dslforum-org:service:Hosts:1">
    <NewIPAddress>192.168.178.40</NewIPAddress><NewActive>1</NewActive>
    </u:GetSpecificHostEntryResponse></s:Body></s:Envelope>"""
    assert parse_host_entry(xml) is True
    assert parse_host_entry(xml.replace("<NewActive>1", "<NewActive>0")) is False
    with pytest.raises(ValueError):
        parse_host_entry("<a/>")


class ScriptedSource:
    name = "scripted"

    def __init__(self, answers: list[object]) -> None:
        self.answers = answers

    async def observe(self, devices: list[TrustedDevice]) -> dict[str, Observation]:
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return {d.id: answer for d in devices}  # type: ignore[misc]


async def test_monitor_updates_mode_and_publishes(tmp_path: Path, db: Database) -> None:
    settings = PresenceSettings(
        home_confirmation_seconds=0,
        away_grace_period_seconds=60,
        devices=[TrustedDevice(id="phone", name="Phone", mac_address="aa:bb:cc:dd:ee:ff")],
    )
    bus = EventBus()
    events = bus.subscribe("t")
    modes = controller(db, bus=bus)
    source = ScriptedSource([SEEN, RuntimeError("router down"), MISS])
    monitor = PresenceMonitor(settings, [source], modes, bus)
    assert await monitor.check(at(0)) is Presence.HOME
    assert modes.effective is Mode.HOME
    assert await monitor.check(at(30)) is Presence.HOME  # source crash = no data
    assert await monitor.check(at(120)) is Presence.AWAY
    assert modes.effective is Mode.AWAY
    types = []
    while not events.empty():
        types.append(events.get_nowait().type)
    assert types.count(EventType.PRESENCE_CHANGED) == 2
    assert EventType.MODE_CHANGED in types
    assert monitor.devices()[0].last_seen == at(0)


# --- direction limits ---------------------------------------------------------------------


def test_auto_home_off_arms_but_never_disarms(db: Database) -> None:
    modes = ModeController(db, None, Mode.HOME, True, "AWAY", auto_home=False)
    modes.update_presence(Presence.AWAY)
    assert modes.effective is Mode.AWAY
    modes.update_presence(Presence.HOME)
    assert modes.effective is Mode.AWAY  # a phone in the Wi-Fi does not disarm
    modes.set_manual(Mode.HOME)
    assert modes.effective is Mode.HOME  # a person still can


def test_auto_away_off_never_arms(db: Database) -> None:
    modes = ModeController(db, None, Mode.HOME, True, "AWAY", auto_away=False)
    modes.update_presence(Presence.AWAY)
    assert modes.effective is Mode.HOME
    modes.set_manual(Mode.AWAY)
    modes.set_auto()
    modes.update_presence(Presence.HOME)
    assert modes.effective is Mode.HOME


async def test_arrival_while_armed_is_announced(db: Database) -> None:
    settings = PresenceSettings(
        home_confirmation_seconds=0,
        away_grace_period_seconds=60,
        devices=[TrustedDevice(id="phone", name="Annas Handy", mac_address="aa:bb:cc:dd:ee:ff")],
    )
    bus = EventBus()
    events = bus.subscribe("t")
    modes = ModeController(db, bus, Mode.HOME, True, "AWAY", auto_home=False)
    monitor = PresenceMonitor(settings, [ScriptedSource([MISS, MISS, SEEN])], modes, bus)
    await monitor.check(at(0))
    await monitor.check(at(60))
    assert modes.effective is Mode.AWAY
    await monitor.check(at(120))
    assert modes.effective is Mode.AWAY
    arrivals = []
    while not events.empty():
        event = events.get_nowait()
        if event.type is EventType.ARRIVAL_DETECTED:
            arrivals.append(event)
    assert arrivals and arrivals[0].data == {"devices": "Annas Handy"}
