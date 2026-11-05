"""The optional web dashboard (aiohttp), running inside the pi-guard process.

A single-page app in `static/` talks to a JSON API under `/api`. Every API
route except login, language texts and the CA certificate needs a session;
every state-changing request also needs the session's CSRF token.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import socket
import ssl
from collections.abc import Awaitable, Callable, Coroutine
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from aiohttp import web

from pi_guard.security.files import atomic_write
from pi_guard.web.auth import (
    PENDING_ALL,
    PENDING_CODEWORD,
    PENDING_NONE,
    AuthStore,
    Session,
    totp_match,
)
from pi_guard.web.security import Guard
from pi_guard.web.tls import ensure_certificates, server_context

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

COOKIE = "pg_session"
PUBLIC = {"/api/session", "/api/login", "/api/login/totp", "/api/logout", "/ca.crt",
          "/api/login/telegram", "/api/login/telegram/poll", "/api/login/webauthn/begin",
          "/api/login/webauthn/finish", "/api/agent/ws", "/api/hello",
          "/api/mobile/session", "/api/mobile/ws", "/api/mobile/report",
          "/api/mobile/emergency", "/api/mobile/calendar", "/api/mobile/vpn", "/api/mobile/reach",
          "/api/mobile/vpn/pq",
          "/api/mobile/app/latest", "/api/mobile/app/apk"}  # fmt: skip
# Reachable while the default password has not been changed yet.
SETUP_ALLOWED = {"/api/account", "/api/account/credentials", "/api/account/totp/begin",
                 "/api/account/totp/confirm", "/api/setup/status"}  # fmt: skip
# What viewers may do: watch. Everything else is closed to them, also anything added later.
VIEWER_GET = {"/api/overview", "/api/events", "/api/stats", "/api/alerts", "/api/connection",
              "/api/evidence", "/api/account"}  # fmt: skip
VIEWER_POST = {"/api/me/password", "/api/me/nav", "/api/connection/check", "/api/mobile/pair",
               "/api/account/totp/begin", "/api/account/totp/confirm",
               "/api/account/totp/disable"}  # fmt: skip
MODULES = ("discord", "whatsapp", "email", "mobile", "chatapps")
SETUP_DONE_KEY = "web.setup_done"
TLS_NAMES_KEY = "web.tls_names"
MAX_TLS_NAMES = 16
TOTP_LENGTH = 6
STATIC = Path(str(resources.files("pi_guard.web").joinpath("static")))

DASHBOARD_KEY: web.AppKey[Dashboard] = web.AppKey("dashboard")
SESSION_KEY = web.RequestKey("pg.session", Session)


def dashboard(request: web.Request) -> Dashboard:
    return request.app[DASHBOARD_KEY]


def current(request: web.Request) -> Session:
    session: Session = request[SESSION_KEY]
    return session


def json_body_limit(limit: int) -> Callable[[web.Request], Awaitable[dict[str, Any]]]:
    async def read(request: web.Request) -> dict[str, Any]:
        if request.content_length and request.content_length > limit:
            raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=request.content_length)
        data = await request.json()
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text="expected a JSON object")
        return data

    return read


read_json = json_body_limit(64_000)


async def read_body(request: web.Request, limit: int) -> bytes:
    """All of it. One read only returns what has arrived so far, often just the first part."""
    body = bytearray()
    async for part in request.content.iter_chunked(1 << 16):
        body += part
        if len(body) > limit:
            raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=len(body))
    return bytes(body)


class Dashboard:
    def __init__(self, app: App) -> None:
        self.app = app
        s = app.settings
        self.settings = s
        self.auth = AuthStore(app.db, self._secret_key())
        self.auth.ensure_account()
        if self.auth.account()["must_change"] and "password" in s.dashboard_methods:
            log.warning("dashboard still uses the default login admin/admin123; "
                        "it must be changed at the first login")  # fmt: skip
        self.guard = Guard(
            s.dashboard_rate_limit_per_minute,
            s.dashboard_ban_minutes,
            s.dashboard_honeypot,
            s.dashboard_trusted_proxies,
            s.dashboard_tls,
        )
        self.guard.on_event = lambda action, detail: app.db.audit("guard", action, detail)
        if s.dashboard_public_url:
            names = {
                urlsplit(s.dashboard_public_url).hostname or "",
                "localhost",
                socket.gethostname().casefold(),
                f"{socket.gethostname().casefold()}.local",
            }
            self.guard.allowed_hosts = names | {n.casefold() for n in s.dashboard_tls_names}
        from pi_guard.web.api_login import LoginFlows

        self.logins = LoginFlows()
        self.tls_dir = s.data_dir / "tls"
        self.microphones: dict[str, Any] = {}  # device -> api_live.MicrophoneStream
        self.gravity_state = False
        self.evidence_running: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._ssl: ssl.SSLContext | None = None
        self.wg_peers = None
        if s.wireguard_enabled:
            from pi_guard.network.wgpeers import PeerStore

            self.wg_peers = PeerStore(app.db, s.data_dir, self._secret_key())
            self.wg_peers.write_file()
            from pi_guard.web.api_network import apply_quantum

            apply_quantum(self)
        self.web = self._build()

    def tls_names(self) -> list[str]:
        return [*self.settings.dashboard_tls_names, *(self.app.db.get(TLS_NAMES_KEY) or [])]

    async def learn_name(self, host: str | None) -> None:
        """A name this device is reached by but cannot see itself, like one from a local DNS.

        Programs check the certificate against the name they use, so it must be listed there.
        New connections get the new certificate at once.
        """
        known = self.tls_names()
        custom = self.settings.dashboard_tls_cert and self.settings.dashboard_tls_key
        if self._ssl is None or custom or not host or host in known or len(known) >= MAX_TLS_NAMES:
            return
        if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host):
            return
        self.app.db.set(TLS_NAMES_KEY, [*(self.app.db.get(TLS_NAMES_KEY) or []), host])
        _, cert, key = await asyncio.to_thread(ensure_certificates, self.tls_dir, self.tls_names())
        self._ssl.load_cert_chain(cert, key)

    def background(self, work: Coroutine[Any, Any, None]) -> None:
        """Run something longer than a request; a reference keeps the task alive."""
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _secret_key(self) -> bytes:
        """PI_GUARD_DASHBOARD_SECRET_KEY, or a key generated once in the data directory."""
        configured = self.settings.dashboard_secret_key
        if configured and configured.get_secret_value():
            return configured.get_secret_value().encode()
        path = self.settings.data_dir / "dashboard.secret"
        if not path.exists():
            atomic_write(path, secrets.token_hex(32).encode())
        return path.read_bytes().strip()

    def _build(self) -> web.Application:
        from pi_guard.web import (
            api_account,
            api_addons,
            api_agents,
            api_core,
            api_export,
            api_live,
            api_login,
            api_manage,
            api_mobile,
            api_network,
            api_security,
            api_system,
            api_tools,
            api_updates,
            api_v1,
        )

        webapp = web.Application(
            middlewares=[self.guard.middleware, self._auth_middleware],
            client_max_size=60 * 1024 * 1024,  # uploads of enrollment videos
        )
        webapp[DASHBOARD_KEY] = self
        webapp.on_response_prepare.append(self._headers)
        webapp.router.add_get("/", self._index)
        webapp.router.add_get("/robots.txt", self._robots)
        webapp.router.add_get("/sw.js", self._service_worker)
        webapp.router.add_get("/manifest.webmanifest", self._manifest)
        webapp.router.add_get("/ca.crt", self._ca)
        webapp.router.add_get("/branding/logo", self._logo)
        webapp.router.add_static("/static/", STATIC, append_version=True)
        webapp.router.add_get("/api/session", self._session)
        webapp.router.add_post("/api/login", self._login)
        webapp.router.add_post("/api/login/totp", self._login_totp)
        webapp.router.add_post("/api/logout", self._logout)
        for module in (api_account, api_addons, api_agents, api_core, api_export, api_login,
                       api_live, api_manage, api_network, api_security, api_system,
                       api_mobile, api_tools, api_updates, api_v1):  # fmt: skip
            webapp.add_routes(module.routes())
        for plugin in self.app.addons.plugins:
            sub = web.Application()
            sub.add_routes(plugin.routes())
            webapp.add_subapp(f"/api/plugins/{plugin.id}/", sub)
        if self.settings.whatsapp_enabled:
            webapp.router.add_get("/webhook/whatsapp", self._whatsapp_verify)
            webapp.router.add_post("/webhook/whatsapp", self._whatsapp_message)
        return webapp

    async def _whatsapp_verify(self, request: web.Request) -> web.Response:
        """Meta's one-time check of the webhook address."""
        token = self.settings.whatsapp_verify_token
        offered = request.query.get("hub.verify_token", "")
        if token is None or not secrets.compare_digest(offered, token.get_secret_value()):
            raise web.HTTPForbidden()
        return web.Response(text=request.query.get("hub.challenge", ""))

    async def _whatsapp_message(self, request: web.Request) -> web.Response:
        from pi_guard.messaging.whatsapp.provider import WhatsAppProvider, valid_signature

        secret = self.settings.whatsapp_app_secret
        body = await read_body(request, 1_000_000)
        signature = request.headers.get("X-Hub-Signature-256", "")
        if secret is None or not valid_signature(secret.get_secret_value(), body, signature):
            raise web.HTTPForbidden()
        provider = self.app.providers.get("whatsapp")
        if isinstance(provider, WhatsAppProvider):
            provider.received(json.loads(body))
        return web.Response(text="ok")

    async def run(self) -> None:
        ssl_context = None
        if self.settings.dashboard_tls:
            if self.settings.dashboard_tls_cert and self.settings.dashboard_tls_key:
                cert, key = self.settings.dashboard_tls_cert, self.settings.dashboard_tls_key
            else:
                _, cert, key = ensure_certificates(self.tls_dir, self.tls_names())
            ssl_context = self._ssl = server_context(cert, key)
        runner = web.AppRunner(self.web, access_log=None, shutdown_timeout=5)
        await runner.setup()
        site = web.TCPSite(runner, self.settings.dashboard_host, self.settings.dashboard_port,
                           ssl_context=ssl_context)  # fmt: skip
        await site.start()
        scheme = "https" if ssl_context else "http"
        log.info("dashboard on %s://%s:%d", scheme, self.settings.dashboard_host,
                 self.settings.dashboard_port)  # fmt: skip
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    # --- middleware ---------------------------------------------------------------------

    async def _headers(self, request: web.Request, response: web.StreamResponse) -> None:
        self.guard.headers(response, request)

    @web.middleware
    async def _auth_middleware(
        self, request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
    ) -> web.StreamResponse:
        path = request.path
        if (
            not path.startswith("/api/")
            or path in PUBLIC
            or path.startswith(("/api/i18n/", "/api/updates/"))
        ):
            return await handler(request)
        bearer = request.headers.get("Authorization", "")
        if path.startswith(("/api/v1/", "/api/plugins/")) and bearer.startswith("Bearer "):
            return await self._token_request(request, handler, bearer[7:].strip())
        if path.startswith("/api/v1/"):
            raise web.HTTPUnauthorized(text="API token required")
        session = self._resolve(request)
        if session is None or session.pending:
            raise web.HTTPUnauthorized(text="login required")
        if path not in SETUP_ALLOWED and self._setup_required(session):
            raise web.HTTPForbidden(text="setup_required")
        if self.auth.role_of(session.username) == "viewer" and not self._viewer_may(request):
            raise web.HTTPForbidden(text="web.viewer_forbidden")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            token = request.headers.get("X-CSRF-Token", "")
            if not secrets.compare_digest(token, session.csrf):
                raise web.HTTPForbidden(text="invalid CSRF token")
            origin = request.headers.get("Origin")
            public = self.settings.dashboard_public_url
            if origin and origin.split("://", 1)[-1] != request.host and origin != public:
                raise web.HTTPForbidden(text="cross-origin request")
        request[SESSION_KEY] = session
        return await handler(request)

    async def _token_request(
        self,
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
        token: str,
    ) -> web.StreamResponse:
        ip = self.guard.client_ip(request)
        found = self.auth.check_token(token[:100])
        if found is None:
            self.guard.login_failed(ip)
            self.app.db.audit(f"web:{ip}", "login.failed", "api token")
            raise web.HTTPUnauthorized(text="invalid token")
        if request.method not in ("GET", "HEAD") and found["scope"] != "control":
            raise web.HTTPForbidden(text="this token may only read")
        request[SESSION_KEY] = Session(f"api:{found['name']}", "", False)
        return await handler(request)

    def modules(self) -> dict[str, bool]:
        """Optional messengers: shown only if wanted. On by default if already in use."""
        saved = self.app.db.get("web.modules") or {}
        return {m: bool(saved.get(m, getattr(self.settings, f"{m}_enabled"))) for m in MODULES}

    @staticmethod
    def _viewer_may(request: web.Request) -> bool:
        path = request.path
        if request.method not in ("GET", "HEAD"):
            return path in VIEWER_POST
        is_video = path.startswith("/api/live/") and path.endswith("/video")  # listening is not
        return path in VIEWER_GET or is_video or path.startswith("/api/media/")

    def _setup_required(self, session: Session) -> bool:
        """The default password must be replaced before anything else works."""
        if "password" not in self.settings.dashboard_methods or session.username == "local":
            return False
        return bool(self.auth.account()["must_change"])

    def _resolve(self, request: web.Request) -> Session | None:
        if "none" in self.settings.dashboard_methods:
            return Session("local", "none", False)
        return self.auth.session(request.cookies.get(COOKIE))

    # --- pages --------------------------------------------------------------------------

    async def _index(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(STATIC / "index.html")

    async def _service_worker(self, request: web.Request) -> web.FileResponse:
        response = web.FileResponse(STATIC / "sw.js")
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    async def _manifest(self, request: web.Request) -> web.Response:
        """With the name chosen under Appearance, which also goes for the installed app."""
        manifest = json.loads((STATIC / "manifest.webmanifest").read_text())
        name = (self.app.db.get("web.appearance") or {}).get("name")
        if name:
            manifest.update(name=name, short_name=name)
        return web.Response(text=json.dumps(manifest), content_type="application/manifest+json")

    async def _robots(self, request: web.Request) -> web.Response:
        return web.Response(text="User-agent: *\nDisallow: /\n")

    async def _logo(self, request: web.Request) -> web.StreamResponse:
        for path in sorted(self.settings.data_dir.glob("logo.*")):
            return web.FileResponse(path, headers={"Cache-Control": "no-cache"})
        return web.FileResponse(STATIC / "img" / "logo.svg")

    async def _ca(self, request: web.Request) -> web.StreamResponse:
        ca = self.tls_dir / "ca.crt"
        if not ca.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(ca, headers={
            "Content-Type": "application/x-x509-ca-cert",
            "Content-Disposition": 'attachment; filename="pi-guard-ca.crt"',
        })  # fmt: skip

    # --- login --------------------------------------------------------------------------

    async def _session(self, request: web.Request) -> web.Response:
        session = self._resolve(request)
        active = session is not None and not session.pending
        return web.json_response({
            "authenticated": bool(session and not session.pending),
            "totp_pending": bool(session and session.pending),
            "username": session.username if session else None,
            "csrf": session.csrf if session and not session.pending else None,
            "methods": self.login_methods(),
            "pending_level": session.level if session else 0,
            "codeword": bool(session and self.auth.is_owner(session.username)
                             and self.auth.has_codeword),
            "totp": bool(session and self.auth.totp_secret(session.username)),
            "setup_required": bool(active and session and self._setup_required(session)),
            "setup_done": bool(self.app.db.get(SETUP_DONE_KEY)),
            "role": self.auth.role_of(session.username) if active and session else None,
            "appearance": self.app.db.get("web.appearance") or {},
            "pinned": (self.app.db.get("web.pinned") or []) if active else [],
            "nav": self.app.db.get(f"web.nav.{session.username}") or [] if session else [],
            "support": (self.app.db.get("web.support") or {}) if active else {},
            "modules": self.modules() if active else {},
            "assistant": bool(
                active and self.app.addons.assistant
                and session and self.auth.role_of(session.username) == "admin"
            ),
            "language": self.settings.language,
        })  # fmt: skip

    def login_methods(self) -> list[str]:
        methods = set(self.settings.dashboard_methods)
        if self.auth.credentials():
            methods.add("webauthn")  # a registered key is meant to be used
        if not self.settings.telegram_ready:
            methods.discard("telegram")
        return sorted(methods)

    def set_cookie(self, response: web.Response, token: str) -> None:
        secure = self.settings.dashboard_tls or bool(self.settings.dashboard_public_url)
        response.set_cookie(COOKIE, token, httponly=True, secure=secure,
                            samesite="Strict", path="/", max_age=7 * 86400)  # fmt: skip

    def _second_step(self, username: str) -> int:
        if self.auth.totp_secret(username) is not None:
            return PENDING_ALL
        owner = self.auth.is_owner(username)
        return PENDING_CODEWORD if owner and self.auth.has_codeword else PENDING_NONE

    async def _login(self, request: web.Request) -> web.Response:
        ip = self.guard.client_ip(request)
        if not self.guard.login_allowed(ip):
            raise web.HTTPTooManyRequests(text="Too many login attempts")
        data = await read_json(request)
        if data.get("website"):  # hidden field only bots fill in
            self.guard.honeypot_hit(ip, "login form")
            raise web.HTTPUnauthorized(text="login failed")
        username = self._check_credentials(data)
        if username is None:
            self.guard.login_failed(ip)
            self.app.db.audit(f"web:{ip}", "login.failed")
            raise web.HTTPUnauthorized(text="login failed")
        pending = self._second_step(username)
        token = self.auth.create_session(username, ip, request.headers.get("User-Agent", ""),
                                         pending=pending)  # fmt: skip
        if not pending:
            self.app.db.audit(f"web:{username}", "login", ip)
        response = web.json_response({"ok": True, "totp_required": bool(pending)})
        self.set_cookie(response, token)
        return response

    def _check_credentials(self, data: dict[str, Any]) -> str | None:
        s = self.settings
        methods = s.dashboard_methods
        keyfile = data.get("keyfile")
        if isinstance(keyfile, str) and "keyfile" in methods:
            name = self.auth.check_keyfile(keyfile[:500])
            return f"key:{name}" if name else None
        username, password = data.get("username"), data.get("password")
        if "password" not in methods or not isinstance(username, str):
            return None
        if not isinstance(password, str) or len(password) > 1000:
            return None
        return self.auth.check_password(username, password)

    def _second_factor(self, code: str, username: str) -> tuple[bool, bool]:
        """(accepted, it was a recovery code). Codes are checked only after the code word."""
        secret = self.auth.totp_secret(username)
        if secret is None:
            return True, False
        owner = self.auth.is_owner(username)
        if owner and len(code.replace(" ", "")) > TOTP_LENGTH:
            return self.auth.use_recovery_code(code), True
        step = totp_match(secret, code)
        used = "account" if owner else username
        return step is not None and self.auth.totp_step_unused(used, step), False

    async def _login_totp(self, request: web.Request) -> web.Response:
        ip = self.guard.client_ip(request)
        if not self.guard.login_allowed(ip):
            raise web.HTTPTooManyRequests(text="Too many login attempts")
        token = request.cookies.get(COOKIE)
        session = self.auth.session(token)
        if token is None or session is None or not session.pending:
            raise web.HTTPUnauthorized(text="login required")
        data = await read_json(request)
        used_recovery = False
        owner = self.auth.is_owner(session.username)
        passed = not owner or self.auth.check_codeword(str(data.get("codeword", "")))
        if passed and session.level == PENDING_ALL:
            passed, used_recovery = self._second_factor(str(data.get("code", "")), session.username)
        if not passed:
            self.guard.login_failed(ip)
            self.app.db.audit(f"web:{ip}", "login.totp_failed")
            raise web.HTTPUnauthorized(text="wrong code")
        if used_recovery:
            self.app.db.audit(f"web:{session.username}", "login.recovery_code", ip)
        full = self.auth.promote(token)
        self.app.db.audit(f"web:{session.username}", "login", ip)
        response = web.json_response({"ok": True})
        self.set_cookie(response, full)
        return response

    async def _logout(self, request: web.Request) -> web.Response:
        token = request.cookies.get(COOKIE)
        if token:
            self.auth.end(token)
        response = web.json_response({"ok": True})
        response.del_cookie(COOKIE, path="/")
        return response
