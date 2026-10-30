"""Optional MQTT bridge for Home Assistant and other home automation.

Publishes state (retained) and events (not retained). Never publishes images,
audio, face data, names of recognised people or secrets. Inbound commands are
off by default; when enabled, only `<prefix>/command/mode` with HOME, AWAY or
AUTO is accepted, and access control belongs on the broker.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
from typing import TYPE_CHECKING, Any

from pi_guard import __version__
from pi_guard.core.events import Event, EventType
from pi_guard.core.modes import AutoUnavailableError, Mode

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

STATUS_INTERVAL = 60
SAFE_DATA_KEYS = {
    "area",
    "duration",
    "peak_area",
    "presence",
    "previous",
    "mode",
    "selection",
    "used_percent",
    "free_mb",
    "version",
    "recognition",
}


def event_payload(event: Event, mode: Mode) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event": event.type.value,
        "camera": event.camera,
        "mode": mode.value,
        "timestamp": event.ts.isoformat(),
    }
    payload.update({k: v for k, v in event.data.items() if k in SAFE_DATA_KEYS})
    return payload


def discovery_configs(prefix: str, node: str, cameras: dict[str, str]) -> dict[str, dict[str, Any]]:
    device = {
        "identifiers": [node],
        "name": "pi-guard",
        "manufacturer": "pi-guard",
        "sw_version": __version__,
    }
    availability = {"availability_topic": f"{prefix}/status"}
    configs: dict[str, dict[str, Any]] = {
        f"homeassistant/sensor/{node}/mode/config": {
            "name": "Mode",
            "unique_id": f"{node}_mode",
            "state_topic": f"{prefix}/mode",
            "icon": "mdi:shield-home",
            "device": device,
            **availability,
        },
        f"homeassistant/sensor/{node}/presence/config": {
            "name": "Presence",
            "unique_id": f"{node}_presence",
            "state_topic": f"{prefix}/presence",
            "device": device,
            **availability,
        },
    }
    for camera_id, name in cameras.items():
        configs[f"homeassistant/binary_sensor/{node}/{camera_id}_motion/config"] = {
            "name": f"{name} motion",
            "unique_id": f"{node}_{camera_id}_motion",
            "state_topic": f"{prefix}/cameras/{camera_id}/motion",
            "device_class": "motion",
            "payload_on": "ON",
            "payload_off": "OFF",
            "device": device,
            **availability,
        }
        configs[f"homeassistant/binary_sensor/{node}/{camera_id}_online/config"] = {
            "name": f"{name} camera",
            "unique_id": f"{node}_{camera_id}_online",
            "state_topic": f"{prefix}/cameras/{camera_id}/status",
            "device_class": "connectivity",
            "payload_on": "running",
            "payload_off": "offline",
            "device": device,
            **availability,
        }
    return configs


class MqttBridge:
    def __init__(self, app: App) -> None:
        self._app = app
        s = app.settings
        self._prefix = s.mqtt_topic_prefix
        self._node = "pi_guard_" + "".join(c if c.isalnum() else "_" for c in socket.gethostname())
        # Subscribed once, so events raised while the broker is down are kept (bounded).
        self._events = app.bus.subscribe("mqtt", maxsize=200)
        self.connected = False

    def topic(self, *parts: str) -> str:
        return "/".join((self._prefix, *parts))

    async def run(self) -> None:
        import aiomqtt

        s = self._app.settings
        password = s.mqtt_password.get_secret_value() if s.mqtt_password else None
        assert s.mqtt_host is not None
        async with aiomqtt.Client(
            s.mqtt_host,
            s.mqtt_port,
            username=s.mqtt_username,
            password=password,
            identifier=self._node,
            tls_params=aiomqtt.TLSParameters() if s.mqtt_tls else None,
            will=aiomqtt.Will(self.topic("status"), "offline", qos=1, retain=True),
        ) as client:
            self.connected = True
            log.info("connected to MQTT broker %s:%d", s.mqtt_host, s.mqtt_port)
            try:
                await client.publish(self.topic("status"), "online", qos=1, retain=True)
                if s.mqtt_home_assistant_discovery:
                    names = {cid: p.camera.name for cid, p in self._app.cameras.items()}
                    for topic, config in discovery_configs(self._prefix, self._node, names).items():
                        await client.publish(topic, json.dumps(config), qos=1, retain=True)
                await self._publish_state(client)
                tasks = [self._forward_events(client), self._periodic_state(client)]
                if s.mqtt_commands_enabled:
                    tasks.append(self._commands(client))
                await asyncio.gather(*tasks)
            finally:
                self.connected = False
                # The broker only sends the will on an unclean disconnect.
                with contextlib.suppress(Exception):
                    await client.publish(self.topic("status"), "offline", qos=1, retain=True)

    async def _publish_state(self, client: Any) -> None:
        state = self._app.modes.state
        await client.publish(self.topic("mode"), state.effective.value, retain=True)
        await client.publish(self.topic("mode", "selection"), state.selection.value, retain=True)
        await client.publish(self.topic("presence"), state.presence.value, retain=True)
        for camera_id, pipeline in self._app.cameras.items():
            await client.publish(
                self.topic("cameras", camera_id, "status"), pipeline.status.state.value, retain=True
            )

    async def _periodic_state(self, client: Any) -> None:
        while True:
            await asyncio.sleep(STATUS_INTERVAL)
            await self._publish_state(client)

    async def _forward_events(self, client: Any) -> None:
        while True:
            event = await self._events.get()
            mode = self._app.modes.effective
            await client.publish(self.topic("events"), json.dumps(event_payload(event, mode)))
            if event.type in (EventType.MOTION_DETECTED, EventType.MOTION_ENDED) and event.camera:
                value = "ON" if event.type is EventType.MOTION_DETECTED else "OFF"
                await client.publish(
                    self.topic("cameras", event.camera, "motion"), value, retain=True
                )
            if event.type in (
                EventType.MODE_CHANGED,
                EventType.PRESENCE_CHANGED,
                EventType.CAMERA_DISCONNECTED,
                EventType.CAMERA_RECONNECTED,
            ):
                await self._publish_state(client)

    async def _commands(self, client: Any) -> None:
        topic = self.topic("command", "mode")
        await client.subscribe(topic, qos=1)
        async for message in client.messages:
            if str(message.topic) != topic:
                continue
            self.apply_command(bytes(message.payload or b""))

    def apply_command(self, payload: bytes) -> bool:
        value = payload.decode(errors="replace").strip().upper()
        modes = self._app.modes
        if value in ("HOME", "AWAY"):
            modes.set_manual(Mode(value), actor="mqtt")
        elif value == "AUTO":
            try:
                modes.set_auto(actor="mqtt")
            except AutoUnavailableError:
                log.warning("MQTT asked for AUTO but presence detection is disabled")
                return False
        else:
            log.warning("ignoring invalid MQTT mode command")
            return False
        return True
