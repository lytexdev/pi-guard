"""Signal through a local signal-cli daemon (JSON-RPC over TCP).

Signal has no official bot API. signal-cli is an unofficial client that must
be registered or linked to a phone number and run as its own service:

    signal-cli daemon --tcp 127.0.0.1:7583

pi-guard only connects to it. While signal-cli is not running, pi-guard waits and
tries again; Signal messages fail and are retried, nothing else is affected. Signal
has no buttons, so menus are rendered as numbered lists and answered with the number.
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
RECONNECT_SECONDS = 60


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
    if sender == account:
        # With the owner's own number, "Note to Self" is the chat with pi-guard. It arrives
        # as a copy from the phone. Anything sent to other people stays ignored.
        sent = (envelope.get("syncMessage") or {}).get("sentMessage") or {}
        to_self = (sent.get("destinationNumber") or sent.get("destination")) == account
        data = sent if to_self else None
    if not data or not sender:
        return None  # receipts, typing indicators
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
        self._waiting = False

    @property
    def connected(self) -> bool:
        return self._writer is not None

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """signal-cli not running is a state, not a crash: say so once and keep trying."""
        while True:
            try:
                connection = await asyncio.open_connection(self._host, self._port, limit=LINE_LIMIT)
            except OSError:
                if not self._waiting:
                    log.warning(
                        "signal-cli is not running at %s:%d; Signal stays offline until it "
                        "does (see scripts/signal.sh)", self._host, self._port,
                    )  # fmt: skip
                self._waiting = True
                await asyncio.sleep(RECONNECT_SECONDS)
            else:
                self._waiting = False
                return connection

    async def run(self, on_message: MessageHandler) -> None:
        reader, writer = await self._connect()
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
        params: dict[str, Any] = {"account": self._account, "recipient": [chat_id],
                                  "message": render(reply)}  # fmt: skip
        if reply.media is not None:
            # signal-cli reads the file itself, so it must run as the pi-guard user.
            params["attachments"] = [str(reply.media)]
        await self._call("send", params)


async def rpc(address: str, method: str, params: dict[str, Any] | None = None,
              wait: float = 10.0) -> Any:  # fmt: skip
    """One request on its own connection, for setup steps outside the running provider."""
    host, _, port = address.rpartition(":")
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, int(port), limit=LINE_LIMIT), 5
    )
    request = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": "rpc"}
    try:
        writer.write(json.dumps(request).encode() + b"\n")
        await writer.drain()
        while True:
            line = await asyncio.wait_for(reader.readline(), wait)
            if not line:
                raise ConnectionError("signal-cli closed the connection")
            message = json.loads(line)
            if message.get("id") == "rpc":
                if "error" in message:
                    raise RuntimeError(message["error"].get("message", "error"))
                return message.get("result")
    finally:
        writer.close()


async def probe(address: str, wait: float = 10.0) -> str:
    """Ask a signal-cli daemon for its version; used by `pi-guard signal test`."""
    result = await rpc(address, "version", wait=wait) or {}
    return str(result.get("version", "unknown"))
