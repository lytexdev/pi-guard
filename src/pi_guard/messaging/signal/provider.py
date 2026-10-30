"""Signal through a local signal-cli daemon (JSON-RPC over TCP).

Signal has no official bot API. signal-cli is an unofficial client that must
be registered or linked to a phone number and run as its own service:

    signal-cli -a +4915... daemon --tcp 127.0.0.1:7583

pi-guard only connects to it. If signal-cli is down, Signal messages fail and
are retried; nothing else is affected. Signal has no buttons, so menus are
rendered as numbered lists and answered with the number.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any

from pi_guard.messaging.base import Incoming, MessageHandler, ProviderUnavailableError, Reply

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 120.0
LINE_LIMIT = 16 * 1024 * 1024


def render(reply: Reply) -> str:
    buttons = [b for row in reply.buttons for b in row]
    if not buttons:
        return reply.text
    menu = "\n".join(f"{number}  {button.label}" for number, button in enumerate(buttons, 1))
    return f"{reply.text}\n\n{menu}"


def parse_envelope(params: dict[str, Any], account: str) -> Incoming | None:
    envelope = params.get("envelope") or {}
    data = envelope.get("dataMessage")
    sender = envelope.get("sourceNumber") or envelope.get("source")
    if not data or not sender or sender == account:
        return None  # receipts, typing indicators, sync messages from our own devices
    text = data.get("message") or ""
    group = data.get("groupInfo") or data.get("groupV2")
    if not text.strip():
        return None
    return Incoming(
        provider="signal",
        sender_id=sender,
        chat_id=sender,
        text=text,
        is_group=group is not None,
        sender_name=envelope.get("sourceName") or "",
    )


class SignalProvider:
    name = "signal"
    supports_buttons = False

    def __init__(self, account: str, address: str) -> None:
        self._account = account
        host, _, port = address.rpartition(":")
        self._host, self._port = host, int(port)
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._ids = itertools.count(1)
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def connected(self) -> bool:
        return self._writer is not None

    async def run(self, on_message: MessageHandler) -> None:
        reader, writer = await asyncio.open_connection(self._host, self._port, limit=LINE_LIMIT)
        self._writer = writer
        log.info("connected to signal-cli at %s:%d", self._host, self._port)
        try:
            while line := await reader.readline():
                self._dispatch(line, on_message)
        finally:
            self._writer = None
            writer.close()
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ProviderUnavailableError("signal-cli disconnected"))
            self._pending.clear()
        raise ConnectionError("signal-cli closed the connection")

    def _dispatch(self, line: bytes, on_message: MessageHandler) -> None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            log.warning("ignoring malformed line from signal-cli")
            return
        request_id = message.get("id")
        if request_id is not None and request_id in self._pending:
            future = self._pending.pop(request_id)
            if "error" in message:
                future.set_exception(RuntimeError(message["error"].get("message", "error")))
            else:
                future.set_result(message.get("result"))
            return
        if message.get("method") == "receive":
            incoming = parse_envelope(message.get("params") or {}, self._account)
            if incoming is not None:
                task = asyncio.create_task(on_message(incoming))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def _call(self, method: str, params: dict[str, Any]) -> Any:
        writer = self._writer
        if writer is None:
            raise ProviderUnavailableError("signal-cli is not connected")
        request_id = str(next(self._ids))
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": request_id}
        try:
            writer.write(json.dumps(payload).encode() + b"\n")
            await writer.drain()
            return await asyncio.wait_for(future, REQUEST_TIMEOUT)
        except (ConnectionError, TimeoutError) as exc:
            raise ProviderUnavailableError(str(exc)) from exc
        finally:
            self._pending.pop(request_id, None)

    async def send(self, chat_id: str, reply: Reply) -> None:
        params: dict[str, Any] = {"recipient": [chat_id], "message": render(reply)}
        if reply.media is not None:
            # signal-cli reads the file itself, so it must run as the pi-guard user.
            params["attachments"] = [str(reply.media)]
        await self._call("send", params)


async def probe(address: str, wait: float = 10.0) -> str:
    """Ask a signal-cli daemon for its version; used by `pi-guard signal test`."""
    host, _, port = address.rpartition(":")
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, int(port)), wait)
    try:
        writer.write(b'{"jsonrpc":"2.0","method":"version","id":"probe"}\n')
        await writer.drain()
        while True:
            line = await asyncio.wait_for(reader.readline(), wait)
            if not line:
                raise ConnectionError("signal-cli closed the connection")
            message = json.loads(line)
            if message.get("id") == "probe":
                result = message.get("result") or {}
                return str(result.get("version", "unknown"))
    finally:
        writer.close()
