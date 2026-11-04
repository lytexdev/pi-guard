"""Discord through a bot: a small client for the gateway and REST API, on aiohttp.

The connection goes out to Discord, so no port has to be opened. pi-guard talks to people in
direct messages only: a home camera does not belong in a server channel, and it means the
bot needs no privileged intent. Discord only allows a bot to write to people who share a
server with it, so the bot has to be invited to a server once (see docs/discord.md).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from pi_guard.messaging.base import (
    AttachmentKind,
    Button,
    Incoming,
    MessageHandler,
    ProviderUnavailableError,
    Reply,
)
from pi_guard.security.files import secure_dir

log = logging.getLogger(__name__)

API = "https://discord.com/api/v10"
GATEWAY_QUERY = "?v=10&encoding=json"
DIRECT_MESSAGES = 1 << 12  # the only intent needed; text of direct messages is always included
TEXT_LIMIT = 2000
ROW_SIZE = 5
MAX_ROWS = 5
LABEL_LIMIT = 80
CUSTOM_ID_LIMIT = 100
UPLOAD_LIMIT = 10_000_000  # what a bot may send without a boosted server
DOWNLOAD_LIMIT = 25_000_000
REQUEST_TIMEOUT = 120
RECONNECT_SECONDS = 5
RATE_LIMIT_TRIES = 3
FATAL_CLOSE_CODES = {4004: "token refused", 4013: "invalid intents", 4014: "intents not allowed"}
ATTACHMENT_HOSTS = {"cdn.discordapp.com", "media.discordapp.net"}
OP_DISPATCH, OP_HEARTBEAT, OP_IDENTIFY, OP_RECONNECT = 0, 1, 2, 7
OP_INVALID_SESSION, OP_HELLO, OP_HEARTBEAT_ACK = 9, 10, 11
COMPONENT_INTERACTION = 3
DEFERRED_UPDATE = 6


class DiscordFatalError(Exception):
    """Trying again will not help until the settings change."""


class _Reconnect(Exception):
    """Discord asked for a new connection, or the old one died."""


def chunks(text: str) -> list[str]:
    """Split on line ends where possible, so a long list stays readable."""
    parts = []
    while len(text) > TEXT_LIMIT:
        cut = text.rfind("\n", 0, TEXT_LIMIT)
        cut = cut if cut > 0 else TEXT_LIMIT
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return [*parts, text]


def components(buttons: tuple[tuple[Button, ...], ...]) -> list[dict[str, Any]]:
    """Rows of at most five buttons, five rows at most, as Discord allows."""
    usable = []
    for row in buttons:
        kept = [b for b in row if len(b.command.encode()) <= CUSTOM_ID_LIMIT]
        if len(kept) != len(row):
            log.error("button command too long for discord")
        usable.append(kept)
    rows = [row[i : i + ROW_SIZE] for row in usable for i in range(0, len(row), ROW_SIZE)]
    if len(rows) > MAX_ROWS:  # one button per row is common in menus
        flat = [b for row in rows for b in row]
        rows = [flat[i : i + ROW_SIZE] for i in range(0, len(flat), ROW_SIZE)][:MAX_ROWS]
    return [
        {"type": 1, "components": [
            {"type": 2, "style": 2, "label": b.label[:LABEL_LIMIT], "custom_id": b.command}
            for b in row
        ]}
        for row in rows if row
    ]  # fmt: skip


def attachment_kind(item: dict[str, Any]) -> AttachmentKind | None:
    kind = str(item.get("content_type") or "").split("/")[0]
    return {"image": "photo", "video": "video", "audio": "audio"}.get(kind)  # type: ignore[return-value]


class DiscordProvider:
    name = "discord"
    supports_buttons = True

    def __init__(
        self,
        token: str,
        upload_dir: Path,
        max_upload_bytes: int,
        may_upload: Callable[[str], bool],
        api: str = API,
    ) -> None:
        token = token.strip().removeprefix("Bot ").strip()  # often copied with the prefix
        self._headers = {"Authorization": f"Bot {token}", "User-Agent": "pi-guard"}
        self._token = token
        self._api = api
        self._upload_dir = upload_dir
        self._max_upload_bytes = max_upload_bytes
        self._may_upload = may_upload
        self._http: aiohttp.ClientSession | None = None
        self._channels: dict[str, str] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._ready = False
        self._sequence: int | None = None
        self.username: str | None = None
        self.bot_id: str | None = None
        self.problem: str | None = None

    @property
    def connected(self) -> bool:
        return self._ready

    async def run(self, on_message: MessageHandler) -> None:
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            self._http = http
            try:
                while True:
                    try:
                        await self._session(http, on_message)
                    except DiscordFatalError as exc:
                        # Trying again cannot help: say why once and wait for new settings.
                        self.problem = str(exc)
                        self._ready = False
                        log.error("discord stays offline: %s", exc)
                        await asyncio.Event().wait()
                    except (_Reconnect, aiohttp.ClientError, OSError, TimeoutError) as exc:
                        log.info("discord connection lost (%s)", type(exc).__name__)
                    self._ready = False
                    await asyncio.sleep(RECONNECT_SECONDS)
            finally:
                self._ready = False
                self._http = None

    async def _gateway_url(self, http: aiohttp.ClientSession) -> str:
        async with http.get(f"{self._api}/gateway/bot", headers=self._headers) as response:
            if response.status == 401:
                raise DiscordFatalError("token refused")
            if response.status >= 400:
                raise _Reconnect
            return str((await response.json())["url"]) + GATEWAY_QUERY

    async def _session(self, http: aiohttp.ClientSession, on_message: MessageHandler) -> None:
        url = await self._gateway_url(http)
        async with http.ws_connect(url, max_msg_size=0, timeout=aiohttp.ClientWSTimeout()) as ws:
            beat: asyncio.Task[None] | None = None
            acknowledged = [True]
            try:
                async for frame in ws:
                    if frame.type is not aiohttp.WSMsgType.TEXT:
                        break
                    message = json.loads(frame.data)
                    code = message.get("op")
                    if message.get("s") is not None:
                        self._sequence = message["s"]
                    if code == OP_HELLO:
                        interval = message["d"]["heartbeat_interval"] / 1000
                        beat = asyncio.create_task(self._heartbeat(ws, interval, acknowledged))
                        await ws.send_json(self._identify())
                    elif code == OP_HEARTBEAT_ACK:
                        acknowledged[0] = True
                    elif code == OP_HEARTBEAT:
                        await ws.send_json({"op": OP_HEARTBEAT, "d": self._sequence})
                    elif code in (OP_RECONNECT, OP_INVALID_SESSION):
                        raise _Reconnect
                    elif code == OP_DISPATCH:
                        self._event(message.get("t"), message.get("d") or {}, on_message)
            finally:
                if beat is not None:
                    beat.cancel()
            if ws.close_code in FATAL_CLOSE_CODES:
                raise DiscordFatalError(FATAL_CLOSE_CODES[ws.close_code])
        raise _Reconnect

    def _identify(self) -> dict[str, Any]:
        return {"op": OP_IDENTIFY, "d": {
            "token": self._token, "intents": DIRECT_MESSAGES,
            "properties": {"os": "linux", "browser": "pi-guard", "device": "pi-guard"},
        }}  # fmt: skip

    async def _heartbeat(
        self, ws: aiohttp.ClientWebSocketResponse, interval: float, acknowledged: list[bool]
    ) -> None:
        while True:
            await asyncio.sleep(interval)
            if not acknowledged[0]:  # a dead connection often looks alive from here
                await ws.close()
                return
            acknowledged[0] = False
            await ws.send_json({"op": OP_HEARTBEAT, "d": self._sequence})

    def _event(self, name: str | None, data: dict[str, Any], on_message: MessageHandler) -> None:
        if name == "READY":
            user = data.get("user") or {}
            self.bot_id, self.username = str(user.get("id", "")), user.get("username")
            self._ready = True
            log.info("discord connected as %s", self.username)
        elif name == "MESSAGE_CREATE":
            self._spawn(self._on_message(data, on_message))
        elif name == "INTERACTION_CREATE" and data.get("type") == COMPONENT_INTERACTION:
            self._spawn(self._on_button(data, on_message))

    def _spawn(self, work: Any) -> None:
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _incoming(self, author: dict[str, Any], text: str, **extra: Any) -> Incoming:
        name = author.get("global_name") or author.get("username") or ""
        user = str(author["id"])
        return Incoming(provider=self.name, sender_id=user, chat_id=user, text=text,
                        sender_name=name, **extra)  # fmt: skip

    async def _on_message(self, data: dict[str, Any], on_message: MessageHandler) -> None:
        author = data.get("author") or {}
        if not author.get("id") or author.get("bot") or data.get("guild_id"):
            return
        self._channels[str(author["id"])] = str(data["channel_id"])
        text = data.get("content") or ""
        item = next(iter(data.get("attachments") or []), None)
        kind = attachment_kind(item) if item else None
        if item is None or kind is None or not self._may_upload(str(author["id"])):
            if text:
                await on_message(self._incoming(author, text))
            return
        limit = self._max_upload_bytes if kind == "audio" else DOWNLOAD_LIMIT
        if int(item.get("size") or 0) > limit:
            await on_message(self._incoming(author, text, attachment_meta={"too_large": True}))
            return
        path = await self._download(item)
        meta = {"duration": item.get("duration_secs"), "mime": item.get("content_type")}
        await on_message(self._incoming(author, text, attachment=path, attachment_kind=kind,
                                        attachment_meta=meta))  # fmt: skip

    async def _on_button(self, data: dict[str, Any], on_message: MessageHandler) -> None:
        author = data.get("user") or {}
        command = (data.get("data") or {}).get("custom_id")
        if data.get("guild_id") or not author.get("id") or not command:
            return
        # Within three seconds, or Discord shows the person an error.
        url = f"{self._api}/interactions/{data['id']}/{data['token']}/callback"
        assert self._http is not None
        async with self._http.post(url, json={"type": DEFERRED_UPDATE}):
            pass
        locale = str(data.get("locale") or "").split("-")[0] or None
        await on_message(self._incoming(author, command, from_button=True, language_hint=locale))

    async def _download(self, item: dict[str, Any]) -> Path:
        url = str(item.get("url") or "")
        if urlsplit(url).hostname not in ATTACHMENT_HOSTS:
            raise ProviderUnavailableError("unexpected attachment address")
        assert self._http is not None
        secure_dir(self._upload_dir)
        suffix = Path(str(item.get("filename") or "upload")).suffix.lower()[:8]
        path = self._upload_dir / f"{uuid.uuid4().hex}{suffix}"
        async with self._http.get(url) as response:
            response.raise_for_status()
            body = await response.content.read(DOWNLOAD_LIMIT + 1)
            while len(body) <= DOWNLOAD_LIMIT and (more := await response.content.read(1 << 20)):
                body += more
        if len(body) > DOWNLOAD_LIMIT:
            raise ProviderUnavailableError("attachment too large")
        await asyncio.to_thread(path.write_bytes, body)
        path.chmod(0o600)
        return path

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        http = self._http
        if http is None:
            raise ProviderUnavailableError("discord is not connected")
        build = kwargs.pop("build", None)
        for _ in range(RATE_LIMIT_TRIES):
            data = {"data": build()} if build else {}
            try:
                async with http.request(method, f"{self._api}{path}", headers=self._headers,
                                        **kwargs, **data) as response:  # fmt: skip
                    if response.status == 429:
                        wait = (await response.json()).get("retry_after", 1)
                        await asyncio.sleep(min(float(wait), 30))
                        continue
                    if response.status in (401, 403, 404):
                        log.warning("discord refused a message (%d)", response.status)
                        raise RuntimeError("recipient unreachable")
                    if response.status >= 400:
                        raise ProviderUnavailableError(f"discord answered {response.status}")
                    return await response.json() if response.status != 204 else None
            except (aiohttp.ClientError, TimeoutError) as exc:
                raise ProviderUnavailableError(type(exc).__name__) from exc
        raise ProviderUnavailableError("rate limited")

    async def _channel(self, user_id: str) -> str:
        channel = self._channels.get(user_id)
        if channel is None:
            answer = await self._request("POST", "/users/@me/channels",
                                         json={"recipient_id": user_id})  # fmt: skip
            channel = self._channels[user_id] = str(answer["id"])
        return channel

    async def send(self, chat_id: str, reply: Reply) -> None:
        if not self._ready:
            raise ProviderUnavailableError("discord is not connected")
        channel = await self._channel(chat_id)
        url = f"/channels/{channel}/messages"
        parts = chunks(reply.text)
        for part in parts[:-1]:
            await self._request("POST", url, json={"content": part})
        payload: dict[str, Any] = {"content": parts[-1]}
        rows = components(reply.buttons)
        if rows:
            payload["components"] = rows
        media = reply.media
        if media is None or media.stat().st_size > UPLOAD_LIMIT:
            if media is not None:
                log.warning("file too large for discord: %d bytes", media.stat().st_size)
            await self._request("POST", url, json=payload)
            return
        content = await asyncio.to_thread(media.read_bytes)

        def form() -> aiohttp.FormData:  # a form can only be sent once; rate limits send it again
            data = aiohttp.FormData()
            data.add_field("payload_json", json.dumps(payload), content_type="application/json")
            data.add_field("files[0]", content, filename=media.name)
            return data

        await self._request("POST", url, build=form)


async def probe(token: str, api: str = API) -> str:
    """The bot's name; used by `pi-guard discord test`."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as http:
        headers = {"Authorization": f"Bot {token}", "User-Agent": "pi-guard"}
        async with http.get(f"{api}/users/@me", headers=headers) as response:
            if response.status == 401:
                raise DiscordFatalError("token refused")
            response.raise_for_status()
            return str((await response.json()).get("username"))
