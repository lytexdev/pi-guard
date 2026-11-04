"""Smart home devices over MQTT: switch lights and plugs, read sensors.

Devices come from `addons.smarthome.devices` and, if wanted, from Home Assistant
discovery messages (Zigbee2MQTT, Tasmota, ESPHome and others announce themselves
that way). Whatever arrives over MQTT is untreated text from the network: values are
shortened and only ever displayed. Commands go only to known devices' command topics.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pi_guard.config.files import SmartDevice

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

DISCOVERY_TOPICS = ("homeassistant/+/+/config", "homeassistant/+/+/+/config")
COMPONENTS = {"switch": "switch", "light": "light", "sensor": "sensor", "binary_sensor": "sensor"}
MAX_DEVICES = 200
MAX_PAYLOAD = 16_384
VALUE_LIMIT = 40
_TEMPLATE_RE = re.compile(r"value_json(?:\.|\[['\"])([A-Za-z0-9_]+)")


class SmartHomeError(Exception):
    """`key` is a translation key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


@dataclass
class Live:
    device: SmartDevice
    value: str | None = None
    discovered: bool = False

    @property
    def on(self) -> bool | None:
        if self.device.kind == "sensor" or self.value is None:
            return None
        if self.value.casefold() in ("on", "true", "1"):
            return True
        if self.value.casefold() in ("off", "false", "0"):
            return False
        return None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")[:48] or "device"


def from_discovery(topic: str, payload: bytes) -> SmartDevice | None:
    """Translate a Home Assistant discovery message; None if it is not usable."""
    component = topic.split("/")[1]
    kind = COMPONENTS.get(component)
    if kind is None or not payload or len(payload) > MAX_PAYLOAD:
        return None
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None

    def get(long: str, short: str) -> Any:
        value = data.get(long, data.get(short))
        base = str(data.get("~", ""))
        if isinstance(value, str) and base:
            if value.startswith("~"):
                return base + value[1:]
            if value.endswith("~"):
                return value[:-1] + base
        return value

    state = get("state_topic", "stat_t")
    command = get("command_topic", "cmd_t")
    if not state and not command:
        return None
    device = data.get("device", data.get("dev"))
    owner = device.get("name", "") if isinstance(device, dict) else ""
    label = str(data.get("name") or "").strip()
    name = f"{owner} {label}".strip() or str(data.get("uniq_id") or topic.split("/")[-2])
    template = str(get("value_template", "val_tpl") or "")
    match = _TEMPLATE_RE.search(template)
    value_key = match.group(1) if match else None
    on, off = str(get("payload_on", "pl_on") or "ON"), str(get("payload_off", "pl_off") or "OFF")
    if data.get("schema") == "json":  # JSON lights take {"state": "ON"}
        on, off, value_key = '{"state":"ON"}', '{"state":"OFF"}', "state"
    identifier = str(data.get("unique_id", data.get("uniq_id")) or topic)
    if command and any(c in str(command) for c in "+#"):
        return None
    return SmartDevice(
        id="d_" + _slug(identifier)[:30], name=name[:60], kind=kind,
        state_topic=str(state) if state else None,
        command_topic=str(command) if command and kind != "sensor" else None,
        payload_on=on, payload_off=off, value_key=value_key,
        unit=str(get("unit_of_measurement", "unit_of_meas") or "")[:10],
    )  # fmt: skip


def read_value(device: SmartDevice, payload: bytes) -> str | None:
    text = payload.decode(errors="replace").strip()
    if device.value_key:
        try:
            data = json.loads(text)
        except ValueError:
            return None
        if not isinstance(data, dict) or device.value_key not in data:
            return None
        text = str(data[device.value_key])
    return text[:VALUE_LIMIT]


class SmartHome:
    def __init__(self, app: App) -> None:
        self._app = app
        self._settings = app.config.file.addons.smarthome
        self.live: dict[str, Live] = {d.id: Live(d) for d in self._settings.devices}
        self._client: Any = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    def _by_topic(self, topic: str) -> list[Live]:
        return [item for item in self.live.values() if item.device.state_topic == topic]

    async def _discovered(self, client: Any, topic: str, payload: bytes) -> None:
        try:
            device = from_discovery(topic, payload)
        except ValueError:  # announced by something on the network; just not usable
            return
        if device is None:
            return
        known = self.live.get(device.id)
        if known is not None and not known.discovered:
            return  # a device configured by hand wins
        if known is None and len(self.live) >= MAX_DEVICES:
            return
        self.live[device.id] = Live(device, known.value if known else None, discovered=True)
        if device.state_topic:
            await client.subscribe(device.state_topic)

    async def run(self) -> None:
        import aiomqtt

        s = self._app.settings
        if not s.mqtt_host:
            await asyncio.Event().wait()
        assert s.mqtt_host is not None
        password = s.mqtt_password.get_secret_value() if s.mqtt_password else None
        async with aiomqtt.Client(
            s.mqtt_host, s.mqtt_port, username=s.mqtt_username, password=password,
            identifier=f"pi_guard_home_{socket.gethostname()}"[:64],
            tls_params=aiomqtt.TLSParameters() if s.mqtt_tls else None,
        ) as client:  # fmt: skip
            self._client = client
            log.info("smart home connected to MQTT broker")
            try:
                for item in self.live.values():
                    if item.device.state_topic:
                        await client.subscribe(item.device.state_topic)
                if self._settings.discovery:
                    for topic in DISCOVERY_TOPICS:
                        await client.subscribe(topic)
                async for message in client.messages:
                    await self._received(client, str(message.topic), bytes(message.payload))
            finally:
                self._client = None

    async def _received(self, client: Any, topic: str, payload: bytes) -> None:
        if topic.startswith("homeassistant/") and topic.endswith("/config"):
            await self._discovered(client, topic, payload)
            return
        if len(payload) > MAX_PAYLOAD:
            return
        for item in self._by_topic(topic):
            value = read_value(item.device, payload)
            if value is not None:
                item.value = value

    def find(self, text: str) -> Live | None:
        wanted = text.strip().casefold()
        if wanted in self.live:
            return self.live[wanted]
        matches = [i for i in self.live.values() if wanted in i.device.name.casefold()]
        return matches[0] if len(matches) == 1 else None

    async def switch(self, device_id: str, on: bool | None) -> Live:
        """Turn on, off, or (None) the opposite of the current state."""
        item = self.live.get(device_id)
        if item is None or item.device.kind == "sensor" or not item.device.command_topic:
            raise SmartHomeError("smarthome.not_switchable")
        if self._client is None:
            raise SmartHomeError("smarthome.offline")
        wanted = (not item.on) if on is None else on
        payload = item.device.payload_on if wanted else item.device.payload_off
        await self._client.publish(item.device.command_topic, payload, qos=1)
        if not item.device.state_topic:  # no feedback from this device
            item.value = "ON" if wanted else "OFF"
        return item

    def overview(self) -> list[dict[str, Any]]:
        rows = []
        for item in sorted(self.live.values(), key=lambda i: i.device.name.casefold()):
            device = item.device
            rows.append({
                "id": device.id, "name": device.name, "kind": device.kind, "on": item.on,
                "value": item.value, "unit": device.unit, "discovered": item.discovered,
                "switchable": device.kind != "sensor" and bool(device.command_topic),
            })  # fmt: skip
        return rows
