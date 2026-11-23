"""Computer-Connect: media, files, ringing and texts for computers with the agent.

What a computer allows is set there, in its agent.conf (CONNECT, CONNECT_USER); the Pi only
sends fixed actions. Files pass through: the upload is read in pieces and each piece goes on to
the computer at once, nothing is stored on the Pi.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import secrets
from typing import Any

from aiohttp import web

from pi_guard.addons.agents import AgentError, AgentHub
from pi_guard.i18n import t
from pi_guard.web.server import current, dashboard, read_json

MEDIA_ACTIONS = ("status", "play-pause", "next", "previous", "stop", "volume-up", "volume-down",
                 "mute")  # fmt: skip
CHUNK = 512 * 1024  # base64 makes it about 700 kB per message, well below both sides' limits
MAX_FILE = 2 * 1024**3  # as MAX_FILE in agent/src/connect.rs
MAX_TEXT = 4000
PLAYER_PREFIX = "org.mpris.MediaPlayer2."


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/connect", computers),
        web.get("/api/connect/{id:\\d+}/media", media_status),
        web.post("/api/connect/{id:\\d+}/media", media),
        web.post("/api/connect/{id:\\d+}/ring", ring),
        web.post("/api/connect/{id:\\d+}/text", text),
        web.post("/api/connect/{id:\\d+}/file", send_file),
    ]


def _hub(request: web.Request) -> AgentHub:
    hub = dashboard(request).app.addons.agents
    if hub is None:
        raise web.HTTPConflict(text="web.addon_not_running")
    return hub


def _id(request: web.Request) -> int:
    return int(request.match_info["id"])


def _actor(request: web.Request) -> str:
    return f"web:{current(request).username}"


def _lang(request: web.Request) -> str:
    return dashboard(request).settings.language


async def _call(work: Any) -> web.Response:
    try:
        return web.json_response(await work)
    except AgentError as exc:
        raise web.HTTPConflict(text=exc.key) from None
    except RuntimeError as exc:
        raise web.HTTPBadGateway(text=str(exc)[:200]) from None


async def computers(request: web.Request) -> web.Response:
    """Every computer with the agent and what it allows; old agents allow nothing here."""
    rows = []
    for agent in _hub(request).list():
        info = agent["info"] or {}
        rows.append({"id": agent["id"], "name": agent["name"], "online": agent["online"],
                     "os": info.get("os"), "agent": info.get("agent"),
                     # None: an agent before 0.5, which has no Computer-Connect at all
                     "connect": info.get("connect")})  # fmt: skip
    return web.json_response(rows)


def _player(value: object) -> str | None:
    """A player's bus name as the agent listed it; the agent takes it only if it is open."""
    player = str(value or "")
    if not player:
        return None
    if not player.startswith(PLAYER_PREFIX) or len(player) > 200:
        raise web.HTTPBadRequest(text="bad player")
    return player


async def media_status(request: web.Request) -> web.Response:
    body: dict[str, Any] = {"cmd": "media", "action": "status"}
    if player := _player(request.query.get("player")):
        body["player"] = player
    return await _call(_hub(request).request(_id(request), **body))


async def media(request: web.Request) -> web.Response:
    data = await read_json(request)
    action = str(data.get("action", ""))
    if action not in MEDIA_ACTIONS:
        raise web.HTTPBadRequest(text="unknown action")
    body: dict[str, Any] = {"cmd": "media", "action": action}
    if player := _player(data.get("player")):
        body["player"] = player
    if action != "status":
        dashboard(request).app.db.audit(
            _actor(request), "connect.media", f"{_id(request)} {action}"
        )
    return await _call(_hub(request).request(_id(request), **body))


async def ring(request: web.Request) -> web.Response:
    dashboard(request).app.db.audit(_actor(request), "connect.ring", str(_id(request)))
    note = t("connect.ring_note", _lang(request))
    return await _call(_hub(request).request(_id(request), cmd="ring", note=note))


async def text(request: web.Request) -> web.Response:
    data = await read_json(request)
    words = str(data.get("text", ""))[:MAX_TEXT]
    if not words.strip():
        raise web.HTTPBadRequest(text="web.cc_text_empty")
    dashboard(request).app.db.audit(_actor(request), "connect.text", f"{_id(request)} {len(words)}")
    return await _call(_hub(request).request(_id(request), cmd="text", text=words,
                                             copy=data.get("copy") is True))  # fmt: skip


async def send_file(request: web.Request) -> web.Response:
    """Multipart: `size` first, then `file`. Each piece goes to the computer before the next is
    read; the computer checks the size and the SHA-256 and drops the file if either is off."""
    hub = _hub(request)
    agent_id = _id(request)
    reader = await request.multipart()
    size_part = await reader.next()
    if size_part is None or getattr(size_part, "name", None) != "size":
        raise web.HTTPBadRequest(text="size first")
    try:
        size = int((await size_part.read()).decode())  # type: ignore[union-attr]
    except ValueError:
        raise web.HTTPBadRequest(text="bad size") from None
    if not 0 <= size <= MAX_FILE:
        raise web.HTTPRequestEntityTooLarge(max_size=MAX_FILE, actual_size=size)
    part = await reader.next()
    if part is None or getattr(part, "name", None) != "file":
        raise web.HTTPBadRequest(text="no file")
    name = str(getattr(part, "filename", "") or "file")[:200]
    transfer = secrets.token_hex(8)
    digest = hashlib.sha256()
    sent = 0
    try:
        await hub.request(agent_id, cmd="file", action="begin", transfer=transfer, name=name,
                          size=size)  # fmt: skip
        while True:
            chunk = await part.read_chunk(CHUNK)  # type: ignore[union-attr]
            if not chunk:
                break
            sent += len(chunk)
            if sent > size:
                raise web.HTTPBadRequest(text="more data than announced")
            digest.update(chunk)
            await hub.request(agent_id, cmd="file", action="chunk", transfer=transfer,
                              data=base64.b64encode(chunk).decode())  # fmt: skip
        note = t("connect.received_note", _lang(request))
        result = await hub.request(agent_id, cmd="file", action="end", transfer=transfer,
                                   sha256=digest.hexdigest(), note=note)  # fmt: skip
    except AgentError as exc:
        await _abort(hub, agent_id, transfer)
        raise web.HTTPConflict(text=exc.key) from None
    except RuntimeError as exc:
        await _abort(hub, agent_id, transfer)
        raise web.HTTPBadGateway(text=str(exc)[:200]) from None
    except (web.HTTPException, ConnectionError):
        await _abort(hub, agent_id, transfer)
        raise
    dashboard(request).app.db.audit(_actor(request), "connect.file", f"{agent_id} {sent} bytes")
    return web.json_response({"ok": True, "bytes": sent, "saved": (result or {}).get("saved")})


async def _abort(hub: AgentHub, agent_id: int, transfer: str) -> None:
    # Gone already: the computer drops a stale transfer by itself after two minutes.
    with contextlib.suppress(AgentError, RuntimeError):
        await hub.request(agent_id, cmd="file", action="abort", transfer=transfer)
