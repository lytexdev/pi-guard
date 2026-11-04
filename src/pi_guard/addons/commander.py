"""Do things by asking: "Licht im Schlafzimmer aus", "fahre meinen PC herunter".

Understanding is separated from doing. A sentence becomes a proposal, first by simple rules
that run here and cost nothing, then, if allowed, by the AI, which only picks from the fixed
list below. Everything it returns is checked against that list and against what exists. A proposal
changes nothing: a person has to confirm it, and only admins can. Remote commands need a
second confirmation, as everywhere. Text from documents never gets near this module.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from pi_guard.addons.agents import AgentError
from pi_guard.addons.jobs import MAX_RECORD_MINUTES, next_time
from pi_guard.audio.service import AudioError
from pi_guard.core.modes import Mode
from pi_guard.i18n import t
from pi_guard.recording.evidence import MAX_MINUTES

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

PENDING_SECONDS = 180
ALARM_SECONDS = 60
ON_WORDS = (" an", " ein", " anmachen", " einschalten", " on")
OFF_WORDS = (" aus", " ausmachen", " ausschalten", " off", "abschalten")
PLAN_PROMPT = (
    "You turn a request for a home security system into JSON. Reply with JSON only, no text. "
    'Use {{"action": name, ...arguments}} with one of: alarm, alarm_stop, mode(mode: HOME|AWAY), '
    "video(camera, minutes), photo(camera), vpn(name), switch(device, on: true|false), "
    "state(device), remote(name), record(start: HH:MM, end: HH:MM), look(camera), block(domain, "
    'minutes). If none fits reply {{"action": "none"}}. Cameras: {cameras}. Devices: {devices}. '
    "Remote commands: {remote}."
)


@dataclass
class Proposal:
    action: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Pending:
    proposal: Proposal
    actor: str
    created: float
    second: bool = False


@dataclass(frozen=True)
class Outcome:
    text: str
    pending: str | None = None  # token: ask for confirmation
    photo: Any = None
    video: Any = None


Sender = Callable[[str], Awaitable[None]]


def _has(text: str, *words: str) -> bool:
    return any(word in text for word in words)


class Commander:
    def __init__(self, app: App) -> None:
        self._app = app
        self._pending: dict[str, _Pending] = {}

    # --- understanding ------------------------------------------------------------------

    def _camera(self, text: str) -> str | None:
        for camera_id, pipeline in self._app.cameras.items():
            names = (pipeline.camera.name, pipeline.camera.room, camera_id)
            if any(n and n.casefold() in text for n in names):
                return camera_id
        return next(iter(self._app.cameras), None) if len(self._app.cameras) == 1 else None

    def _device(self, text: str) -> Any:
        home = self._app.addons.smarthome
        if home is None:
            return None
        best, score = None, 0
        for live in home.live.values():
            words = re.findall(r"\w{3,}", live.device.name.casefold())
            hits = sum(1 for w in words if w in text)
            if hits > score:
                best, score = live, hits
        return best

    def understand(self, text: str) -> Proposal | None:
        """Rules for everyday sentences: no tokens, nothing leaves the Pi."""
        s = f" {' '.join(text.casefold().split())} ".replace("?", " ").replace(".", " ")
        times = re.search(r"von (\d{1,2})(?::(\d\d))?(?: uhr)? bis (\d{1,2})(?::(\d\d))?", s)
        if times and _has(s, "mikrofon", "ton", "audio", "aufnahme", "aufnehmen"):
            start = f"{int(times.group(1)):02d}:{times.group(2) or '00'}"
            end = f"{int(times.group(3)):02d}:{times.group(4) or '00'}"
            return Proposal("record", {"start": start, "end": end})
        if "alarm" in s:
            if _has(s, " aus", "stopp", "stop", "beend"):
                return Proposal("alarm_stop")
            if _has(s, "spiel", " an ", "auslös", "start", "play", "los"):
                return Proposal("alarm")
        minutes = re.search(r"(\d{1,2}) ?(?:min|minuten)", s)
        if minutes and _has(s, "video", "aufnehm", "filme"):
            return Proposal("video", {"camera": self._camera(s), "minutes": int(minutes.group(1))})
        if _has(s, "vpn") and _has(s, "zugang", "access", "erstell", "create"):
            found = re.search(r"(?:für|for|namens|name) ([\w -]{2,30})", s)
            return Proposal("vpn", {"name": (found.group(1).strip() if found else "").title()})
        if _has(s, " foto", " photo", " bild ") and _has(s, "mach", "take", "schick", "send"):
            return Proposal("photo", {"camera": self._camera(s)})
        if _has(s, "schau", "was siehst", "was ist zu sehen", "look at"):
            return Proposal("look", {"camera": self._camera(s), "question": text})
        power_words = ("herunter", "runter", "ausschalten", "shut down", "neustart")
        agents = self._app.addons.agents
        if agents is not None and _has(s, *power_words):
            known = agents.list()
            agent = agents.find(s)
            if agent is None and len(known) == 1 and _has(s, " pc ", "computer", "rechner"):
                agent = known[0]["id"]
            if agent is not None:
                command = "reboot" if "neustart" in s else "shutdown"
                return Proposal("agent_power", {"agent": agent, "command": command})
        remote = self._app.addons.remote
        if remote is not None and _has(
            s, "herunter", "runter", "ausschalten", "shut down", "neustart"
        ):
            for action in remote.actions.values():
                words = re.findall(r"\w{2,}", action.name.casefold())
                if any(w in s for w in words):
                    return Proposal("remote", {"name": action.id})
        if _has(s, "wie lange", "wie warm", "wie kalt", "wie viel", "status", "temperatur"):
            device = self._device(s)
            if device is not None:
                return Proposal("state", {"device": device.device.id})
        device = self._device(s)
        if device is not None and device.device.kind != "sensor":
            if _has(s, *OFF_WORDS):
                return Proposal("switch", {"device": device.device.id, "on": False})
            if _has(s, *ON_WORDS):
                return Proposal("switch", {"device": device.device.id, "on": True})
        if _has(s, "ich bin weg", "ich gehe", "scharf"):
            return Proposal("mode", {"mode": "AWAY"})
        if _has(s, "ich bin da", "ich bin zuhause", "bin wieder da"):
            return Proposal("mode", {"mode": "HOME"})
        return None

    async def _ask_ai(self, text: str) -> Proposal | None:
        assistant = self._app.addons.assistant
        if assistant is None or not assistant.settings.ai_actions or not assistant.ready:
            return None
        home = self._app.addons.smarthome
        remote = self._app.addons.remote
        system = PLAN_PROMPT.format(
            cameras=[p.camera.name for p in self._app.cameras.values()],
            devices=[i.device.name for i in home.live.values()] if home else [],
            remote=[a.name for a in remote.actions.values()] if remote else [],
        )
        answer = await assistant.plan(system, text)
        match = re.search(r"\{.*\}", answer, re.DOTALL)
        try:
            data = json.loads(match.group(0)) if match else {}
        except ValueError:
            return None
        return self._checked(data, text)

    def _checked(self, data: dict[str, Any], text: str) -> Proposal | None:
        """Nothing from the AI is used as it is."""
        action = str(data.get("action", ""))
        home = self._app.addons.smarthome
        if action in ("alarm", "alarm_stop"):
            return Proposal(action)
        if action == "mode" and data.get("mode") in ("HOME", "AWAY"):
            return Proposal("mode", {"mode": data["mode"]})
        if action in ("video", "photo", "look"):
            camera = self._camera(str(data.get("camera", "")).casefold())
            minutes = (
                int(data.get("minutes") or 5) if str(data.get("minutes") or 5).isdigit() else 5
            )
            return Proposal(action, {"camera": camera, "minutes": minutes, "question": text})
        if action == "vpn":
            return Proposal("vpn", {"name": str(data.get("name", ""))[:30]})
        if action in ("switch", "state") and home is not None:
            found = home.find(str(data.get("device", "")))
            if found is None:
                return None
            return Proposal(action, {"device": found.device.id, "on": data.get("on") is True})
        if action == "remote":
            remote = self._app.addons.remote
            wanted = str(data.get("name", "")).casefold()
            for item in remote.actions.values() if remote else []:
                if wanted and wanted in item.name.casefold():
                    return Proposal("remote", {"name": item.id})
        if action == "record" and re.match(r"^\d\d:\d\d$", str(data.get("start", ""))):
            return Proposal("record", {"start": data["start"], "end": str(data.get("end", ""))})
        if action == "block" and data.get("domain"):
            return Proposal("block", {"domain": str(data["domain"]),
                                      "minutes": data.get("minutes")})  # fmt: skip
        return None

    async def propose(self, text: str, lang: str, actor: str) -> Outcome | None:
        """None: this is no command, so it is an ordinary question."""
        proposal = self.understand(text) or await self._ask_ai(text)
        if proposal is None:
            return None
        if proposal.action == "state":  # reading changes nothing
            return Outcome(self._state(proposal, lang))
        return self._offer(proposal, lang, actor)

    def _offer(self, proposal: Proposal, lang: str, actor: str, second: bool = False) -> Outcome:
        self._expire()
        token = secrets.token_hex(6)
        self._pending[token] = _Pending(proposal, actor, time.monotonic(), second)
        key = "cmd.confirm_again" if second else "cmd.confirm"
        return Outcome(t(key, lang, what=self.describe(proposal, lang)), token)

    def _expire(self) -> None:
        limit = time.monotonic() - PENDING_SECONDS
        self._pending = {k: v for k, v in self._pending.items() if v.created > limit}

    def describe(self, p: Proposal, lang: str) -> str:
        a = p.args
        home = self._app.addons.smarthome
        remote = self._app.addons.remote
        camera = self._app.cameras.get(a.get("camera") or "")
        values: dict[str, Any] = {
            "camera": camera.camera.name if camera else "?", "minutes": a.get("minutes", ""),
            "start": a.get("start", ""), "end": a.get("end", ""), "name": a.get("name", ""),
            "domain": a.get("domain", ""),
        }  # fmt: skip
        if p.action == "switch" and home is not None:
            values["name"] = home.live[a["device"]].device.name
            return t("cmd.switch_on" if a["on"] else "cmd.switch_off", lang, **values)
        if p.action == "remote" and remote is not None:
            values["name"] = remote.actions[a["name"]].name
        if p.action == "mode":
            values["mode"] = t(f"mode.{a['mode'].lower()}", lang)
        if p.action == "agent_power" and self._app.addons.agents is not None:
            values["name"] = self._app.addons.agents.name(a["agent"]) or "?"
            values["command"] = t(f"web.ag_cmd_{a['command']}", lang)
        return t(f"cmd.{p.action}", lang, **values)

    def _state(self, p: Proposal, lang: str) -> str:
        home = self._app.addons.smarthome
        assert home is not None
        item = home.live[p.args["device"]]
        if item.value is None:
            return t("cmd.state_unknown", lang, name=item.device.name)
        return t("cmd.state", lang, name=item.device.name,
                 value=f"{item.value} {item.device.unit}".strip())  # fmt: skip

    # --- doing --------------------------------------------------------------------------

    async def answer(
        self, token: str, yes: bool, lang: str, actor: str, deliver: Sender, chat: dict[str, str]
    ) -> Outcome:
        self._expire()
        pending = self._pending.pop(token, None)
        if pending is None or pending.actor != actor:
            return Outcome(t("cmd.expired", lang))
        if not yes:
            return Outcome(t("cmd.cancelled", lang))
        proposal = pending.proposal
        if proposal.action in ("remote", "agent_power") and not pending.second:
            return self._offer(proposal, lang, actor, second=True)
        self._app.db.audit(actor, "assistant.action", proposal.action)
        try:
            return await self._run(proposal, lang, actor, deliver, chat)
        except (AudioError, AgentError, OSError, ValueError, KeyError, RuntimeError) as exc:
            log.warning("action %s failed: %s", proposal.action, type(exc).__name__)
            return Outcome(t("cmd.failed", lang))

    async def _run(
        self, p: Proposal, lang: str, actor: str, deliver: Sender, chat: dict[str, str]
    ) -> Outcome:
        app, a = self._app, p.args
        done = Outcome(t("cmd.done", lang))
        if p.action == "alarm":
            if app.audio is None:
                return Outcome(t("cmd.not_possible", lang))
            app.audio.start_alarm(ALARM_SECONDS)
            return done
        if p.action == "alarm_stop":
            if app.audio is not None:
                await app.audio.stop_alarm()
            return done
        if p.action == "mode":
            app.modes.set_manual(Mode(a["mode"]), actor=actor)
            return done
        if p.action == "switch":
            home = app.addons.smarthome
            assert home is not None
            await home.switch(a["device"], a["on"])
            return done
        if p.action == "remote":
            remote = app.addons.remote
            assert remote is not None
            result = await remote.run(remote.actions[a["name"]])
            return done if result.ok else Outcome(t("cmd.failed", lang))
        if p.action == "agent_power":
            agents = app.addons.agents
            assert agents is not None
            await agents.request(a["agent"], cmd=a["command"])
            return done
        if p.action == "photo":
            path = await app.take_photo(a["camera"])
            return Outcome(t("cmd.done", lang), photo=path)
        if p.action == "look":
            return await self._look(a, lang)
        if p.action == "video":
            return await self._video(a, lang, actor, deliver)
        if p.action == "vpn":
            return self._vpn(a, lang)
        if p.action == "record":
            return self._schedule(a, lang, chat)
        if p.action == "block":
            if app.addons.blocks is None:
                return Outcome(t("cmd.not_possible", lang))
            minutes = int(a["minutes"]) if a.get("minutes") else None
            await app.addons.blocks.add(a["domain"], minutes)
            return done
        return Outcome(t("cmd.not_possible", lang))

    async def _look(self, a: dict[str, Any], lang: str) -> Outcome:
        assistant = self._app.addons.assistant
        if assistant is None or not a.get("camera"):
            return Outcome(t("cmd.not_possible", lang))
        path = await self._app.take_photo(a["camera"])
        text = await assistant.describe(path, str(a.get("question") or "Was ist zu sehen?"), lang)
        return Outcome(text, photo=path)

    async def _video(self, a: dict[str, Any], lang: str, actor: str, deliver: Sender) -> Outcome:
        from pi_guard.recording.evidence import record_evidence

        camera = a.get("camera")
        minutes = min(max(int(a.get("minutes") or 5), 1), MAX_MINUTES)
        if not camera:
            return Outcome(t("cmd.which_camera", lang))

        async def work() -> None:
            try:
                result = await record_evidence(self._app, camera, minutes, actor)
                await deliver(str(result.video))
            except (OSError, ValueError) as exc:
                log.warning("video failed: %s", type(exc).__name__)

        board = self._app.dashboard
        if board is not None:
            board.background(work())
        return Outcome(t("cmd.video_started", lang, minutes=minutes))

    def _vpn(self, a: dict[str, Any], lang: str) -> Outcome:
        board = self._app.dashboard
        store = board.wg_peers if board else None
        if store is None or not a.get("name"):
            return Outcome(t("cmd.not_possible", lang))
        store.add(str(a["name"]))
        return Outcome(t("cmd.vpn_done", lang, name=a["name"]))

    def _schedule(self, a: dict[str, Any], lang: str, chat: dict[str, str]) -> Outcome:
        jobs = self._app.addons.jobs
        if jobs is None or self._app.audio is None or not self._app.audio.recording_enabled:
            return Outcome(t("cmd.not_possible", lang))
        tz = self._app.settings.tz
        start = next_time(str(a["start"]), tz)
        end = next_time(str(a["end"]), tz, after=start)
        minutes = int((end - start) / timedelta(minutes=1))
        if not 1 <= minutes <= MAX_RECORD_MINUTES:
            return Outcome(t("cmd.record_too_long", lang, limit=MAX_RECORD_MINUTES))
        jobs.add("record", start, {"minutes": minutes, **chat})
        when = start.astimezone(tz).strftime("%d.%m. %H:%M")
        return Outcome(t("cmd.record_planned", lang, when=when, minutes=minutes))
