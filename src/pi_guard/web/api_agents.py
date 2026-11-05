"""Connected computers: the connection of the agents and their pages in the dashboard."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

from pi_guard.addons.agents import DOCKER_ACTIONS, AgentError, AgentHub, Connection
from pi_guard.addons.remote import ConfirmationError
from pi_guard.web.server import current, dashboard, read_json

log = logging.getLogger(__name__)

MAX_MESSAGE = 1_000_000


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/agent/ws", agent_socket),
        web.get("/api/agents", agents_list),
        web.post("/api/agents", agents_create),
        web.delete("/api/agents/{id:\\d+}", agents_delete),
        web.get("/api/agents/{id:\\d+}/docker", docker_list),
        web.post("/api/agents/{id:\\d+}/docker", docker_action),
        web.get("/api/agents/{id:\\d+}/docker/{container}/logs", docker_logs),
        web.post("/api/agents/{id:\\d+}/power/begin", power_begin),
        web.post("/api/agents/power/confirm", power_confirm),
    ]


def _hub(request: web.Request) -> AgentHub:
    hub = dashboard(request).app.addons.agents
    if hub is None:
        raise web.HTTPConflict(text="web.addon_not_running")
    return hub


def _actor(request: web.Request) -> str:
    return f"web:{current(request).username}"


def _id(request: web.Request) -> int:
    return int(request.match_info["id"])


async def agent_socket(request: web.Request) -> web.WebSocketResponse:
    d = dashboard(request)
    hub = _hub(request)
    ip = d.guard.client_ip(request)
    header = request.headers.get("Authorization", "")
    agent_id = hub.authenticate(header[7:].strip()[:100]) if header.startswith("Bearer ") else None
    if agent_id is None:
        d.guard.login_failed(ip)
        d.app.db.audit(f"web:{ip}", "login.failed", "agent token")
        raise web.HTTPUnauthorized(text="invalid token")
    socket = web.WebSocketResponse(heartbeat=30, max_msg_size=MAX_MESSAGE)
    await socket.prepare(request)
    connection = Connection(agent_id, socket.send_str)
    hub.attach(connection)
    log.info("agent %s connected", hub.name(agent_id))
    try:
        async for message in socket:
            if message.type == WSMsgType.TEXT:
                hub.received(connection, message.data)
    finally:
        hub.detach(connection)
        log.info("agent %s disconnected", hub.name(agent_id))
    return socket


async def agents_list(request: web.Request) -> web.Response:
    return web.json_response(_hub(request).list())


async def agents_create(request: web.Request) -> web.Response:
    hub = _hub(request)
    try:
        agent_id, token = hub.create(str((await read_json(request)).get("name", "")))
    except AgentError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None
    d = dashboard(request)
    d.app.db.audit(_actor(request), "agent.add", hub.name(agent_id) or "")
    # The agent connects by the name in this browser's address bar, and checks it.
    await d.learn_name(urlsplit(f"//{request.host}").hostname)
    return web.json_response({"id": agent_id, "token": token})


async def agents_delete(request: web.Request) -> web.Response:
    hub = _hub(request)
    agent_id = _id(request)
    if not hub.delete(agent_id):
        raise web.HTTPNotFound()
    connection = hub.connections.get(agent_id)
    if connection is not None:
        hub.detach(connection)
    dashboard(request).app.db.audit(_actor(request), "agent.remove")
    return web.json_response({"ok": True})


async def _call(work: Any) -> web.Response:
    try:
        return web.json_response(await work)
    except AgentError as exc:
        raise web.HTTPConflict(text=exc.key) from None
    except RuntimeError as exc:
        raise web.HTTPBadGateway(text=str(exc)[:200]) from None


async def docker_list(request: web.Request) -> web.Response:
    return await _call(_hub(request).docker(_id(request), "list"))


async def docker_logs(request: web.Request) -> web.Response:
    return await _call(_hub(request).docker(_id(request), "logs", request.match_info["container"]))


async def docker_action(request: web.Request) -> web.Response:
    data = await read_json(request)
    action, container = str(data.get("action", "")), str(data.get("container", ""))
    if action not in DOCKER_ACTIONS:
        raise web.HTTPBadRequest(text="unknown action")
    dashboard(request).app.db.audit(_actor(request), "agent.docker", f"{action} {container[:40]}")
    return await _call(_hub(request).docker(_id(request), action, container))


async def power_begin(request: web.Request) -> web.Response:
    data = await read_json(request)
    try:
        token = _hub(request).begin(_id(request), str(data.get("command", "")), _actor(request))
    except ConfirmationError as exc:
        raise web.HTTPConflict(text=exc.key) from None
    return web.json_response({"token": token})


async def power_confirm(request: web.Request) -> web.Response:
    """First call: token for step two. Second: the name must be typed, then it runs."""
    hub = _hub(request)
    data = await read_json(request)
    actor = _actor(request)
    try:
        agent_id, command, following = hub.confirm(str(data.get("token", "")), actor)
    except ConfirmationError as exc:
        raise web.HTTPConflict(text=exc.key) from None
    name = hub.name(agent_id) or ""
    if following is not None:
        return web.json_response({"token": following, "name": name, "command": command})
    if str(data.get("typed", "")).strip().casefold() != name.casefold():
        raise web.HTTPBadRequest(text="remote.expired")
    dashboard(request).app.db.audit(actor, "agent.power", f"{name}: {command}")
    return await _call(hub.request(agent_id, cmd=command))
