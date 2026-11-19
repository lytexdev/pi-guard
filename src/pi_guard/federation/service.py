"""Both ends of a link: the helper's listener and tunnel, the helped side's connection.

Frames are JSON over one WebSocket that the helped Pi opens:
  hello -> challenge -> auth -> ok         (both sides prove their key, see protocol.py)
  helped -> helper: status, alert, res     (answers carry the helped dashboard's reply)
  helper -> helped: req                    (a dashboard request, checked again on arrival)
  both ways:        note                   (a message for the other household's admins, from
                                            a contact that is reached through the link)
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import logging
import secrets
import ssl
import subprocess
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp
from aiohttp import web

from pi_guard import __version__
from pi_guard.core.events import Event, EventType
from pi_guard.core.modes import Mode
from pi_guard.federation import protocol as p
from pi_guard.federation.store import FederationError, FederationStore, Link
from pi_guard.web.auth import Session

if TYPE_CHECKING:
    from pi_guard.web.server import Dashboard

log = logging.getLogger(__name__)

HANDSHAKE_SECONDS = 10
REQUEST_SECONDS = 30
STATUS_SECONDS = 60
RETRY = (5, 300)  # seconds between attempts, growing
FAILS_PER_IP = 10  # failed handshakes per address and 10 minutes
PARALLEL = 4  # tunnelled requests at a time per link
FORWARD = {EventType.UNKNOWN_PERSON, EventType.PERSON_DETECTED, EventType.EMERGENCY_ESCALATED,
           EventType.CAMERA_DISCONNECTED, EventType.STORAGE_LOW, EventType.RECORDING_FAILED,
           EventType.SERVICE_DOWN}  # fmt: skip
FORWARD_EVERY = 300  # the same kind of alarm at most this often
HEARD_EVERY = 60  # helper side: messages per household and kind, whatever arrives
NOTE_MAX = 3000  # characters of a note; pictures do not go through the link
NOTES_PER_WINDOW = 20  # notes taken from one household per NOTE_WINDOW seconds
NOTE_WINDOW = 600
HEADER = "X-PiGuard-Federation"
HEADER_LINK = "X-PiGuard-Federation-Link"
CERT_DAYS = 3650


@dataclass
class Peer:
    """A helped household connected to this helper right now."""

    link: Link
    ws: web.WebSocketResponse
    pending: dict[int, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)
    counter: int = 0


class Federation:
    def __init__(self, dashboard: Dashboard) -> None:
        self.d = dashboard
        self.app = dashboard.app
        self.settings = dashboard.settings
        self.store = FederationStore(self.app.db, dashboard._secret_key())
        # Only this process knows these: they mark a tunnelled request on the loopback.
        self.secret = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)
        self.peers: dict[int, Peer] = {}
        self.status: dict[int, dict[str, Any]] = {}
        self.alerts: dict[int, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=20))
        self.connected: set[int] = set()  # helped side: links with a live connection
        self._fails: dict[str, deque[float]] = defaultdict(deque)
        self._clients: dict[int, asyncio.Task[None]] = {}
        self._forwarded: dict[tuple[int, str], float] = {}  # (link, kind) -> monotonic
        self._heard: dict[tuple[int, str], float] = {}
        self._notes: dict[int, deque[float]] = defaultdict(deque)  # link -> when notes came
        self._events = self.app.bus.subscribe("federation", 50)
        self._sockets: dict[int, aiohttp.ClientWebSocketResponse] = {}
        self.listen_problem: str | None = None  # translation key while the port cannot be used

    # --- certificate of the listener: its own, long-lived, pinned in every code -------------

    @property
    def cert_dir(self) -> Path:
        return self.d.tls_dir / "federation"

    def certificate(self) -> tuple[Path, Path]:
        from pi_guard.security.files import secure_dir

        folder = secure_dir(self.cert_dir)
        key, cert = folder / "fed.key", folder / "fed.crt"
        if not cert.is_file() or not key.is_file():
            subprocess.run(
                ["openssl", "req", "-x509", "-new", "-newkey", "ec", "-pkeyopt",
                 "ec_paramgen_curve:P-256", "-nodes", "-keyout", str(key), "-out", str(cert),
                 "-days", str(CERT_DAYS), "-subj", "/CN=pi-guard federation"],
                capture_output=True, timeout=60, check=True,
            )  # fmt: skip
            key.chmod(0o600)
        return cert, key

    def pin(self) -> str:
        cert, _ = self.certificate()
        return hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()

    def address(self) -> str:
        """Where helped households reach this Pi: the setting, else the VPN's public name."""
        from urllib.parse import urlsplit

        s = self.settings
        host = s.federation_address.strip()
        if not host and s.dashboard_public_url:
            host = urlsplit(s.dashboard_public_url).hostname or ""
        if not host and s.wireguard_endpoint:
            host = s.wireguard_endpoint.rsplit(":", 1)[0].strip("[]")
        if not host:
            raise FederationError("web.fed_need_address")
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"  # an IPv6 address
        return f"wss://{host}:{s.federation_port}/federation/ws"

    def invite(self) -> str:
        if not self.settings.federation_listen:
            raise FederationError("web.fed_listen_off")
        code = p.Code(self.address(), self.pin(), self.store.public(), self.store.invite(),
                      self.own_name())  # fmt: skip
        return code.encode()

    def own_name(self) -> str:
        appearance = self.app.db.get("web.appearance") or {}
        return p.clean_name(appearance.get("name")) or "pi-guard"

    # --- helper side: the listener ----------------------------------------------------------

    async def listen(self) -> None:
        taken = port_taken_by(self.settings)
        if taken:
            # Retrying cannot help while the other part keeps the port: say so once and wait.
            self.listen_problem = f"web.fed_port_{taken}"
            log.error("linked households cannot listen: port %d is used by the %s; choose "
                      "another federation_port", self.settings.federation_port, taken)  # fmt: skip
            await asyncio.Event().wait()
        cert, key = await asyncio.to_thread(self.certificate)
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert, key)
        # Only the link lives on this port: never the dashboard.
        site_app = web.Application(client_max_size=64 * 1024)
        site_app.router.add_get("/federation/ws", self._accept)
        runner = web.AppRunner(site_app, access_log=None, shutdown_timeout=5)
        await runner.setup()
        # All addresses on purpose: the router forwards this port from outside.
        site = web.TCPSite(runner, None, self.settings.federation_port, ssl_context=context)
        try:
            await site.start()
        except OSError:
            self.listen_problem = "web.fed_port_in_use"
            await runner.cleanup()
            raise
        self.listen_problem = None
        log.info("linked households may connect on port %d", self.settings.federation_port)
        try:
            await asyncio.Event().wait()
        finally:
            for peer in list(self.peers.values()):
                await peer.ws.close()
            await runner.cleanup()

    def _blocked(self, ip: str) -> bool:
        if len(self._fails) > 1000:  # many addresses: forget the oldest
            for old in list(self._fails)[:500]:
                del self._fails[old]
        fails = self._fails[ip]
        while fails and time.monotonic() - fails[0] > 600:
            fails.popleft()
        return len(fails) >= FAILS_PER_IP

    async def _accept(self, request: web.Request) -> web.StreamResponse:
        ip = request.remote or "?"
        if self._blocked(ip):
            raise web.HTTPTooManyRequests()
        ws = web.WebSocketResponse(max_msg_size=p.MAX_FRAME, heartbeat=30)
        await ws.prepare(request)
        link = await self._handshake(ws, ip)
        if link is None:
            self._fails[ip].append(time.monotonic())
            await ws.close()
            return ws
        old = self.peers.get(link.id)
        if old is not None:
            await old.ws.close()
        peer = self.peers[link.id] = Peer(link, ws)
        await asyncio.to_thread(self.store.seen, link.id)
        log.info("linked household %s connected", link.id)
        try:
            async for message in ws:
                if message.type is not aiohttp.WSMsgType.TEXT:
                    break
                await self._from_helped(peer, message.json())
        except (ValueError, TypeError) as exc:
            log.warning("linked household %s sent something wrong: %s", link.id, type(exc).__name__)
        finally:
            if self.peers.get(link.id) is peer:
                del self.peers[link.id]
            for future in peer.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("link closed"))
            await asyncio.to_thread(self.store.seen, link.id)
        return ws

    async def _handshake(self, ws: web.WebSocketResponse, ip: str) -> Link | None:
        try:
            hello = await ws.receive_json(timeout=HANDSHAKE_SECONDS)
            theirs, key = p.unb64(str(hello["nonce"])), p.unb64(str(hello["key"]))
            if hello.get("t") != "hello" or len(theirs) != p.NONCE_BYTES or len(key) != 32:
                return None
            link = await asyncio.to_thread(self.store.by_key, "helper", key)
            token = str(hello.get("pair") or "")
            if link is None and not (token and self.settings.federation_listen):
                return None
            mine = p.new_nonce()
            me = self.store.identity()
            proof = p.proof_message("helper", mine, theirs, key)
            challenge = {"t": "challenge", "v": p.VERSION, "nonce": p.b64(mine),
                         "key": p.b64(self.store.public()), "sig": p.sign(me, proof)}  # fmt: skip
            await ws.send_json(challenge)
            answer = await ws.receive_json(timeout=HANDSHAKE_SECONDS)
            proof = p.proof_message("helped", theirs, mine, self.store.public())
            if answer.get("t") != "auth" or not p.verify(key, str(answer.get("sig")), proof):
                return None
            if link is None:
                # A first visit: only with a valid code, which is gone afterwards.
                if not await asyncio.to_thread(self.store.redeem, token):
                    return None
                link_id = await asyncio.to_thread(
                    self.store.add, "helper", p.clean_name(hello.get("name")), key
                )
                link = self.store.get(link_id)
                self.app.db.audit(f"federation:{ip}", "federation.linked", str(link_id))
                await self.app.messenger.notify_admins(
                    self._t("federation.linked", name=link.name if link else "?")
                )
            if link is None:
                return None
            await ws.send_json({"t": "ok", "id": link.id, "name": self.own_name()})
            return link
        except (TimeoutError, ValueError, KeyError, TypeError, p.ProtocolError, FederationError):
            return None

    async def _from_helped(self, peer: Peer, frame: dict[str, Any]) -> None:
        kind = frame.get("t")
        if kind == "res":
            future = peer.pending.pop(int(frame.get("id", -1)), None)
            if future is not None and not future.done():
                future.set_result(frame)
        elif kind == "status":
            self.status[peer.link.id] = _clean_status(frame.get("status"))
            await asyncio.to_thread(self.store.seen, peer.link.id)
        elif kind == "alert":
            await self._alert(peer.link, frame)
        elif kind == "note":
            await self._note(peer.link, frame)

    async def _alert(self, link: Link, frame: dict[str, Any]) -> None:
        what = str(frame.get("kind", ""))[:40]
        known = {e.value for e in FORWARD} | {"sos"}
        if what not in known:
            return
        camera = p.clean_name(frame.get("camera"))
        entry = {"kind": what, "camera": camera, "ts": str(frame.get("ts", ""))[:40]}
        self.alerts[link.id].appendleft(entry)
        now = time.monotonic()
        if now - self._heard.get((link.id, what), float("-inf")) < HEARD_EVERY:
            return  # shown on the page, but no second message right away
        self._heard[(link.id, what)] = now
        key = "federation.sos" if what == "sos" else "federation.alert"
        lang = self.settings.language
        from pi_guard.i18n import t

        event = t(f"event.{what}", lang) if what != "sos" else ""
        text = self._t(key, name=link.name, event=event, camera=camera or "–")
        await self.app.messenger.notify_admins(text, tone="alert", sos=what == "sos")

    def _t(self, key: str, **values: object) -> str:
        from pi_guard.i18n import t

        return t(key, self.settings.language, **values)

    async def request(self, link_id: int, method: str, path: str, ctype: str,
                      body: bytes) -> dict[str, Any]:  # fmt: skip
        """A request to a helped dashboard; its answer frame, or an error."""
        peer = self.peers.get(link_id)
        if peer is None:
            raise web.HTTPServiceUnavailable(text="web.fed_offline")
        peer.counter += 1
        number = peer.counter
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        peer.pending[number] = future
        try:
            await peer.ws.send_json({"t": "req", "id": number, "m": method, "p": path,
                                     "ct": ctype[:200], "b": p.b64(body)})  # fmt: skip
            return await asyncio.wait_for(future, REQUEST_SECONDS)
        except (TimeoutError, ConnectionError):
            raise web.HTTPGatewayTimeout(text="web.fed_timeout") from None
        finally:
            peer.pending.pop(number, None)

    # --- helped side: the outgoing connections ----------------------------------------------

    async def run(self) -> None:
        """Keep one connection per helper; forward alarms; follow links added or removed."""
        forward = asyncio.create_task(self._forward_events())
        try:
            while True:
                wanted = {link.id: link for link in self.store.links("helped")}
                for link_id, task in list(self._clients.items()):
                    if link_id not in wanted or task.done():
                        task.cancel()
                        del self._clients[link_id]
                        self.connected.discard(link_id)
                for link_id, link in wanted.items():
                    if link_id not in self._clients:
                        self._clients[link_id] = asyncio.create_task(self._keep(link))
                await asyncio.sleep(5)
        finally:
            forward.cancel()
            for task in self._clients.values():
                task.cancel()

    async def _keep(self, link: Link) -> None:
        wait = RETRY[0]
        while True:
            try:
                await self._session(link)
                wait = RETRY[0]
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, OSError, TimeoutError, p.ProtocolError) as exc:
                log.info("helper %s not reached: %s", link.id, type(exc).__name__)
            self.connected.discard(link.id)
            await asyncio.sleep(wait + secrets.randbelow(5))
            wait = min(wait * 2, RETRY[1])

    async def _connect(
        self, http: aiohttp.ClientSession, url: str, pin: str, key: bytes, pair: str = ""
    ) -> tuple[aiohttp.ClientWebSocketResponse, dict[str, Any]]:
        """Open and prove both sides; the helper must hold the key from the code."""
        ws = await http.ws_connect(url, ssl=aiohttp.Fingerprint(bytes.fromhex(pin)),
                                   heartbeat=30, max_msg_size=p.MAX_FRAME,
                                   timeout=aiohttp.ClientWSTimeout(ws_close=10))  # fmt: skip
        try:
            mine = p.new_nonce()
            hello = {"t": "hello", "v": p.VERSION, "key": p.b64(self.store.public()),
                     "nonce": p.b64(mine), "name": self.own_name()}  # fmt: skip
            if pair:
                hello["pair"] = pair
            await ws.send_json(hello)
            challenge = await ws.receive_json(timeout=HANDSHAKE_SECONDS)
            theirs = p.unb64(str(challenge.get("nonce", "")))
            proof = p.proof_message("helper", theirs, mine, self.store.public())
            if (challenge.get("t") != "challenge" or p.unb64(str(challenge.get("key"))) != key
                    or len(theirs) != p.NONCE_BYTES
                    or not p.verify(key, str(challenge.get("sig")), proof)):  # fmt: skip
                raise p.ProtocolError("web.fed_wrong_helper")
            sig = p.sign(self.store.identity(), p.proof_message("helped", mine, theirs, key))
            await ws.send_json({"t": "auth", "sig": sig})
            ok = await ws.receive_json(timeout=HANDSHAKE_SECONDS)
            if ok.get("t") != "ok":
                raise p.ProtocolError("web.fed_refused")
            return ws, ok
        except BaseException:
            await ws.close()
            raise

    async def join(self, code: p.Code, role: str, extras: list[str]) -> int:
        """Enter a helper's code: prove both keys once, then remember the helper."""
        if self.store.by_key("helped", code.key) is not None:
            raise FederationError("web.fed_known")
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as http:
                ws, _ = await self._connect(http, code.url, code.pin, code.key, pair=code.token)
                await ws.close()
        except p.ProtocolError as exc:
            raise FederationError(
                str(exc) if str(exc).startswith("web.") else "web.fed_refused"
            ) from None
        except (aiohttp.ClientError, OSError, TimeoutError, ValueError):
            raise FederationError("web.fed_unreachable") from None
        return self.store.add("helped", code.name, code.key, url=code.url, pin=code.pin,
                              role=role, extras=extras)  # fmt: skip

    async def _session(self, link: Link) -> None:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as http:
            ws, _ = await self._connect(http, link.url, link.pin, link.peer_key)
            self.connected.add(link.id)
            self._sockets[link.id] = ws
            await asyncio.to_thread(self.store.seen, link.id)
            gate = asyncio.Semaphore(PARALLEL)
            tasks: set[asyncio.Task[None]] = set()
            status = asyncio.create_task(self._send_status(ws))
            try:
                async for message in ws:
                    if message.type is not aiohttp.WSMsgType.TEXT:
                        break
                    frame = message.json()
                    current = self.store.get(link.id)  # rights may have changed meanwhile
                    if current is None:
                        break
                    if frame.get("t") == "note":
                        await self._note(current, frame)
                        continue
                    if frame.get("t") != "req":
                        continue
                    task = asyncio.create_task(self._answer(ws, gate, current, frame))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
            finally:
                status.cancel()
                for task in tasks:
                    task.cancel()
                self._sockets.pop(link.id, None)
                await ws.close()

    async def _send_status(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while not ws.closed:
            await ws.send_json({"t": "status", "status": await asyncio.to_thread(self.own_status)})
            await asyncio.sleep(STATUS_SECONDS)

    def own_status(self) -> dict[str, Any]:
        """What a helper sees without opening anything: no pictures, no names of people."""
        app = self.app
        last = app.db.execute("SELECT ts, type FROM events ORDER BY id DESC LIMIT 1").fetchone()
        return {
            "version": __version__,
            "mode": app.modes.effective.value,
            "cameras": [
                {"name": c.camera.name, "online": bool(c.online)} for c in app.cameras.values()
            ],
            "open_alerts": len(app.escalation.open_alerts()),
            "last_event": {"ts": last["ts"], "type": last["type"]} if last else None,
        }

    async def _answer(self, ws: aiohttp.ClientWebSocketResponse, gate: asyncio.Semaphore,
                      link: Link, frame: dict[str, Any]) -> None:  # fmt: skip
        number = int(frame.get("id", 0))
        method = str(frame.get("m", "GET")).upper()
        target = str(frame.get("p", "/"))[:2000]
        path = target.split("?", 1)[0]
        async with gate:
            if method not in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE") or not (
                p.tunnel_allowed(path, link.role, link.extras)
            ):
                await ws.send_json({"t": "res", "id": number, "s": 403, "ct": "text/plain",
                                    "b": p.b64(b"web.fed_forbidden")})  # fmt: skip
                return
            answer = await self._local(link, method, target, str(frame.get("ct", "")),
                                       p.unb64(str(frame.get("b", ""))))  # fmt: skip
            await ws.send_json({"t": "res", "id": number, **answer})

    async def _local(self, link: Link, method: str, target: str, ctype: str,
                     body: bytes) -> dict[str, Any]:  # fmt: skip
        """The request against this Pi's own dashboard, as the helper with the granted role."""
        s = self.settings
        wildcard = s.dashboard_host in ("0.0.0.0", "::", "")  # noqa: S104 - compared, not bound
        host = "127.0.0.1" if wildcard else s.dashboard_host
        if ":" in host:
            host = f"[{host}]"
        scheme = "https" if s.dashboard_tls else "http"
        headers = {HEADER: self.secret, HEADER_LINK: str(link.id), "X-CSRF-Token": self.csrf}
        if ctype:
            headers["Content-Type"] = ctype
        try:
            async with (
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=REQUEST_SECONDS)) as http,
                http.request(method, f"{scheme}://{host}:{s.dashboard_port}{target}",
                             data=body or None, headers=headers, ssl=False,
                             allow_redirects=False) as response,
            ):  # fmt: skip
                data = await response.content.read(p.MAX_BODY + 1)
                if len(data) > p.MAX_BODY:
                    return {"s": 413, "ct": "text/plain", "b": p.b64(b"web.fed_too_big")}
                found = response.headers
                return {
                    "s": response.status,
                    "ct": found.get("Content-Type", ""),
                    "cd": found.get("Content-Disposition", ""),
                    "cc": found.get("Cache-Control", ""),
                    "b": p.b64(data),
                }
        except (aiohttp.ClientError, OSError, TimeoutError):
            return {"s": 502, "ct": "text/plain", "b": p.b64(b"web.fed_local_failed")}

    # --- helped side: alarms and SOS to the helpers -----------------------------------------

    async def _forward_events(self) -> None:
        while True:
            event = await self._events.get()
            with contextlib.suppress(Exception):
                await self._forward(event)

    async def _forward(self, event: Event) -> None:
        if event.type not in FORWARD:
            return
        if event.type is EventType.PERSON_DETECTED and self.app.modes.effective is not Mode.AWAY:
            return
        camera = self.app.cameras.get(event.camera or "")
        frame = {"t": "alert", "kind": event.type.value, "ts": event.ts.isoformat(),
                 "camera": camera.camera.name if camera else ""}  # fmt: skip
        await self._to_helpers(frame, only_alerts=True)

    async def sos(self) -> None:
        """An SOS reaches every helper: it must get through."""
        from pi_guard.core.timeutil import utcnow

        await self._to_helpers({"t": "alert", "kind": "sos", "ts": utcnow().isoformat()}, False)

    async def _to_helpers(self, frame: dict[str, Any], only_alerts: bool) -> None:
        """To each connected helper; the same kind of alarm at most every FORWARD_EVERY."""
        now = time.monotonic()
        for link in self.store.links("helped"):
            ws = self._sockets.get(link.id)
            if ws is None or ws.closed or (only_alerts and "alerts" not in link.extras):
                continue
            key = (link.id, str(frame.get("kind")))
            if only_alerts and now - self._forwarded.get(key, float("-inf")) < FORWARD_EVERY:
                continue
            with contextlib.suppress(aiohttp.ClientError, ConnectionError, RuntimeError):
                await ws.send_json(frame)
                self._forwarded[key] = now

    # --- both sides: notes for the other household's admins -------------------------------

    def reachable(self, link_id: int) -> bool:
        link = self.store.get(link_id)
        if link is None:
            return False
        if link.side == "helper":
            return link.id in self.peers
        ws = self._sockets.get(link.id)
        return ws is not None and not ws.closed

    async def note(self, link_id: int, text: str, urgent: bool = False) -> None:
        """A message to the admins of a linked household, in either direction. Raises
        ProviderUnavailableError when the household is not connected now."""
        from pi_guard.messaging.base import ProviderUnavailableError

        link = self.store.get(link_id)
        frame = {"t": "note", "text": _clean_text(text), "urgent": bool(urgent)}
        if link is None:
            raise ProviderUnavailableError(f"no household link {link_id}")
        if link.side == "helper":
            peer = self.peers.get(link.id)
            ws: Any = peer.ws if peer else None
        else:
            ws = self._sockets.get(link.id)
        if ws is None or ws.closed:
            raise ProviderUnavailableError(f"household {link.name} is not connected")
        try:
            await ws.send_json(frame)
        except (aiohttp.ClientError, ConnectionError, RuntimeError) as exc:
            raise ProviderUnavailableError(str(exc)) from exc

    async def _note(self, link: Link, frame: dict[str, Any]) -> None:
        """A note from the other household: its words, under its name, to this Pi's admins."""
        text = _clean_text(frame.get("text"))
        if not text:
            return
        now = time.monotonic()
        recent = self._notes[link.id]
        while recent and now - recent[0] > NOTE_WINDOW:
            recent.popleft()
        if len(recent) >= NOTES_PER_WINDOW:
            log.warning("too many notes from household %s; dropped one", link.name)
            return
        recent.append(now)
        urgent = bool(frame.get("urgent"))
        words = self._t("federation.note", name=link.name, text=text)
        await self.app.messenger.notify_admins(
            words, tone="alert" if urgent else "info", sos=urgent
        )

    # --- helped side: who a tunnelled request is --------------------------------------------

    def session_for(self, request: web.Request) -> Session | None:
        """A request the tunnel made on this Pi: the secret only this process knows, from this
        machine itself."""
        given = request.headers.get(HEADER)
        if not given or not secrets.compare_digest(given, self.secret):
            return None
        try:
            if not ipaddress.ip_address(request.remote or "").is_loopback and (
                request.remote != self.settings.dashboard_host
            ):
                return None
            link = self.store.get(int(request.headers.get(HEADER_LINK, "")))
        except ValueError:
            return None
        if link is None or link.side != "helped":
            return None
        return Session(f"fed:{link.id}", self.csrf, False)

    def link_of(self, username: str) -> Link | None:
        if not username.startswith("fed:"):
            return None
        try:
            link = self.store.get(int(username[4:]))
        except ValueError:
            return None
        return link if link is not None and link.side == "helped" else None

    def role_of(self, username: str) -> str:
        link = self.link_of(username)
        return link.role if link is not None else "viewer"


def _clean_status(raw: object) -> dict[str, Any]:
    """Only what the page shows, short: a helped Pi must not fill the helper's memory."""
    if not isinstance(raw, dict):
        return {}
    cameras = raw.get("cameras")
    last = raw.get("last_event")
    alerts = str(raw.get("open_alerts", "0"))
    status: dict[str, Any] = {
        "version": str(raw.get("version", ""))[:20],
        "mode": str(raw.get("mode", ""))[:10],
        "cameras": [
            {"name": p.clean_name(c.get("name")), "online": bool(c.get("online"))}
            for c in (cameras if isinstance(cameras, list) else [])[:16]
            if isinstance(c, dict)
        ],
        "open_alerts": min(int(alerts), 9999) if alerts.isdigit() else 0,
        "last_event": None,
        "received": time.time(),
    }
    if isinstance(last, dict):
        status["last_event"] = {"ts": str(last.get("ts", ""))[:40],
                                "type": str(last.get("type", ""))[:40]}  # fmt: skip
    return status


def port_taken_by(settings: Any) -> str | None:
    """Which other part of pi-guard uses the link's port: "dashboard", "blockpage" or None.
    The block page takes 8088 and 8444, the link's default, when it has an address."""
    from pi_guard.addons.blockpage import HTTP_PORT, HTTPS_PORT

    port = settings.federation_port
    if port == settings.dashboard_port:
        return "dashboard"
    if settings.blockpage_ip and port in (HTTP_PORT, HTTPS_PORT):
        return "blockpage"
    return None


def _clean_text(raw: object) -> str:
    """Words only: no control characters but line breaks, and not too long."""
    text = "".join(c for c in str(raw or "") if c == "\n" or c.isprintable())
    return text.strip()[:NOTE_MAX]
