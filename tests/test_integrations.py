from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pi_guard.core.events import Event, EventType
from pi_guard.core.lifecycle import Backoff, ComponentState, supervise
from pi_guard.core.modes import Mode
from pi_guard.integrations.mqtt.bridge import MqttBridge, discovery_configs, event_payload
from pi_guard.messaging.base import Button, Incoming, ProviderUnavailableError, Reply
from pi_guard.messaging.signal.provider import SignalProvider, parse_envelope, probe, render

from .helpers import make_app

ACCOUNT = "+4915100000000"
OWNER = "+4915111111111"


class FakeSignalCli:
    """Speaks the signal-cli JSON-RPC daemon protocol on a local port."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.writers: list[asyncio.StreamWriter] = []
        self.server: asyncio.Server | None = None

    async def start(self) -> str:
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        return f"127.0.0.1:{port}"

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.append(writer)
        while line := await reader.readline():
            request = json.loads(line)
            self.requests.append(request)
            result: Any = (
                {"version": "0.13.0"} if request["method"] == "version" else {"timestamp": 1}
            )
            writer.write(
                json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
                + b"\n"
            )
            await writer.drain()

    async def push(self, message: str, sender: str = OWNER) -> None:
        envelope = {
            "source": sender,
            "sourceNumber": sender,
            "sourceName": "Owner",
            "dataMessage": {"timestamp": 1, "message": message},
        }
        payload = {
            "jsonrpc": "2.0",
            "method": "receive",
            "params": {"envelope": envelope, "account": ACCOUNT},
        }
        for writer in self.writers:
            writer.write(json.dumps(payload).encode() + b"\n")
            await writer.drain()

    async def disconnect(self) -> None:
        for writer in self.writers:
            writer.close()
        self.writers.clear()

    async def stop(self) -> None:
        await self.disconnect()
        if self.server:
            self.server.close()


async def test_signal_receive_and_send() -> None:
    cli = FakeSignalCli()
    address = await cli.start()
    received: list[Incoming] = []

    async def on_message(incoming: Incoming) -> None:
        received.append(incoming)

    provider = SignalProvider(ACCOUNT, address)
    task = asyncio.create_task(provider.run(on_message))
    await asyncio.sleep(0.05)
    assert provider.connected
    await cli.push("foto")
    await asyncio.sleep(0.05)
    assert received[0].text == "foto" and received[0].sender_id == OWNER
    await provider.send(
        OWNER, Reply("Hello", photo=Path("/tmp/x.jpg"), buttons=((Button("Photo", "photo"),),))
    )
    request = cli.requests[-1]
    assert request["method"] == "send"
    assert request["params"]["recipient"] == [OWNER]
    assert request["params"]["attachments"] == ["/tmp/x.jpg"]
    assert request["params"]["message"].endswith("1  Photo")
    assert await probe(address) == "0.13.0"

    await cli.disconnect()
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(task, 1)
    with pytest.raises(ProviderUnavailableError):
        await provider.send(OWNER, Reply("x"))
    await cli.stop()


def test_signal_envelope_filtering() -> None:
    base = {"source": OWNER, "sourceNumber": OWNER, "dataMessage": {"message": "status"}}
    assert parse_envelope({"envelope": base}, ACCOUNT) is not None
    assert parse_envelope({"envelope": {**base, "dataMessage": None}}, ACCOUNT) is None
    assert parse_envelope({"envelope": {**base, "sourceNumber": ACCOUNT}}, ACCOUNT) is None
    group = {**base, "dataMessage": {"message": "status", "groupInfo": {"groupId": "x"}}}
    assert parse_envelope({"envelope": group}, ACCOUNT).is_group  # type: ignore[union-attr]


def test_render_numbered_menu() -> None:
    reply = Reply("Menu", buttons=((Button("Foto", "photo"),), (Button("Video", "video"),)))
    assert render(reply) == "Menu\n\n1  Foto\n2  Video"
    assert render(Reply("plain")) == "plain"


async def test_signal_outage_does_not_affect_other_components() -> None:
    provider = SignalProvider(ACCOUNT, "127.0.0.1:1")  # nothing listens here
    signal_state = ComponentState("provider:signal")
    ticks = 0

    async def recorder() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    async def ignore(incoming: Incoming) -> None:
        return None

    tasks = [
        asyncio.create_task(
            supervise(signal_state, lambda: provider.run(ignore), Backoff(0.01, 0.01))
        ),
        asyncio.create_task(supervise(ComponentState("camera"), recorder)),
    ]
    await asyncio.sleep(0.2)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert signal_state.restarts >= 3 and "ConnectionRefused" in (signal_state.last_error or "")
    assert ticks > 10


# --- MQTT ---------------------------------------------------------------------------------


def test_event_payload_excludes_private_data() -> None:
    event = Event(
        EventType.KNOWN_PERSON,
        camera="door",
        snapshot=Path("/x.jpg"),
        data={"name": "Erna", "score": 0.8},
        ts=datetime(2026, 10, 30, 14, 0, tzinfo=UTC),
    )
    payload = event_payload(event, Mode.AWAY)
    assert payload == {
        "event": "known_person",
        "camera": "door",
        "mode": "AWAY",
        "timestamp": "2026-10-30T14:00:00+00:00",
    }
    motion = event_payload(
        Event(EventType.MOTION_DETECTED, camera="door", data={"area": 2.5}), Mode.HOME
    )
    assert motion["area"] == 2.5


def test_home_assistant_discovery() -> None:
    configs = discovery_configs("pi-guard", "pi_guard_host", {"door": "Front door"})
    motion = configs["homeassistant/binary_sensor/pi_guard_host/door_motion/config"]
    assert motion["state_topic"] == "pi-guard/cameras/door/motion"
    assert motion["device_class"] == "motion"
    assert motion["availability_topic"] == "pi-guard/status"
    assert "homeassistant/sensor/pi_guard_host/mode/config" in configs


def test_mqtt_commands_are_validated(tmp_path: Path) -> None:
    app = make_app(tmp_path, mqtt_enabled=True, mqtt_host="localhost", mqtt_commands_enabled=True)
    bridge = MqttBridge(app)
    assert bridge.apply_command(b"away")
    assert app.modes.effective is Mode.AWAY
    assert not bridge.apply_command(b"rm -rf /")
    assert not bridge.apply_command(b"AUTO")  # presence is not enabled
    assert app.modes.effective is Mode.AWAY
    app.db.close()


async def test_mqtt_unreachable_broker_raises_for_supervisor(tmp_path: Path) -> None:
    pytest.importorskip("aiomqtt")
    app = make_app(tmp_path, mqtt_enabled=True, mqtt_host="127.0.0.1", mqtt_port=1)
    bridge = MqttBridge(app)
    with pytest.raises(Exception, match=r"(?i)connect|refused"):
        await asyncio.wait_for(bridge.run(), 5)
    assert not bridge.connected
    app.db.close()


async def test_mqtt_queue_is_bounded(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    MqttBridge(app)
    for _ in range(500):
        app.bus.publish(Event(EventType.MOTION_DETECTED, camera="living_room"))
    sizes = [q.qsize() for name, q in app.bus._queues if name == "mqtt"]
    assert sizes == [200]
    app.db.close()
