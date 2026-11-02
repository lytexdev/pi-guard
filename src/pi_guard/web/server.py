"""The optional web dashboard (aiohttp), running inside the pi-guard process.

A single-page app in `static/` talks to a JSON API under `/api`. Every API
route except login, language texts and the CA certificate needs a session;
every state-changing request also needs the session's CSRF token.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Awaitable, Callable
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web

from pi_guard.security.files import atomic_write
from pi_guard.web.auth import AuthStore, Session, totp_match
from pi_guard.web.security import Guard
from pi_guard.web.tls import ensure_certificates, server_context

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

COOKIE = "pg_session"
PUBLIC = {"/api/session", "/api/login", "/api/login/totp", "/api/logout", "/ca.crt"}
# Reachable while the default password has not been changed yet.
SETUP_ALLOWED = {"/api/account", "/api/account/credentials", "/api/account/totp/begin",
                 "/api/account/totp/confirm", "/api/setup/status"}  # fmt: skip
SETUP_DONE_KEY = "web.setup_done"
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
        self.tls_dir = s.data_dir / "tls"
        self.microphones: dict[str, Any] = {}  # device -> api_live.MicrophoneStream
        self.wg_peers = None
        if s.wireguard_enabled:
            from pi_guard.network.wgpeers import PeerStore

            self.wg_peers = PeerStore(app.db, s.data_dir, self._secret_key())
            self.wg_peers.write_file()
        self.web = self._build()

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
        from pi_guard.web import api_account, api_core, api_live, api_manage, api_network

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
        webapp.router.add_static("/static/", STATIC, append_version=True)
        webapp.router.add_get("/api/session", self._session)
        webapp.router.add_post("/api/login", self._login)
        webapp.router.add_post("/api/login/totp", self._login_totp)
        webapp.router.add_post("/api/logout", self._logout)
        for module in (api_account, api_core, api_manage, api_live, api_network):
            webapp.add_routes(module.routes())
        return webapp

    async def run(self) -> None:
        ssl_context = None
        if self.settings.dashboard_tls:
            if self.settings.dashboard_tls_cert and self.settings.dashboard_tls_key:
                cert, key = self.settings.dashboard_tls_cert, self.settings.dashboard_tls_key
            else:
                _, cert, key = ensure_certificates(self.tls_dir, self.settings.dashboard_tls_names)
            ssl_context = server_context(cert, key)
        runner = web.AppRunner(self.web, access_log=None, shutdown_timeout=5)
        await runner.setup()
        site = web.TCPSite(runner, self.settings.dashboard_host, self.settings.dashboard_port,
                           ssl_context=ssl_context)  # fmt: skip
        await site.start()
        scheme = "https" if ssl_context else "http"
        log.info("dashboard on %s://%s:%d", scheme, self.settings.dashboard_host,
                 self.settings.dashboard_port)  # fmt: skip
        try:
            import asyncio

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
        if not path.startswith("/api/") or path in PUBLIC or path.startswith("/api/i18n/"):
            return await handler(request)
        session = self._resolve(request)
        if session is None or session.pending:
            raise web.HTTPUnauthorized(text="login required")
        if path not in SETUP_ALLOWED and self._setup_required(session):
            raise web.HTTPForbidden(text="setup_required")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            token = request.headers.get("X-CSRF-Token", "")
            if not secrets.compare_digest(token, session.csrf):
                raise web.HTTPForbidden(text="invalid CSRF token")
            origin = request.headers.get("Origin")
            if origin and origin.split("://", 1)[-1] != request.host:
                raise web.HTTPForbidden(text="cross-origin request")
        request[SESSION_KEY] = session
        return await handler(request)

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

    async def _manifest(self, request: web.Request) -> web.FileResponse:
        response = web.FileResponse(STATIC / "manifest.webmanifest")
        response.content_type = "application/manifest+json"
        return response

    async def _robots(self, request: web.Request) -> web.Response:
        return web.Response(text="User-agent: *\nDisallow: /\n")

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
            "methods": sorted(self.settings.dashboard_methods),
            "setup_required": bool(active and session and self._setup_required(session)),
            "setup_done": bool(self.app.db.get(SETUP_DONE_KEY)),
            "language": self.settings.language,
        })  # fmt: skip

    def _set_cookie(self, response: web.Response, token: str) -> None:
        response.set_cookie(COOKIE, token, httponly=True, secure=self.settings.dashboard_tls,
                            samesite="Strict", path="/", max_age=7 * 86400)  # fmt: skip

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
        pending = self.auth.totp_secret() is not None
        token = self.auth.create_session(username, ip, request.headers.get("User-Agent", ""),
                                         pending=pending)  # fmt: skip
        if not pending:
            self.app.db.audit(f"web:{username}", "login", ip)
        response = web.json_response({"ok": True, "totp_required": pending})
        self._set_cookie(response, token)
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

    async def _login_totp(self, request: web.Request) -> web.Response:
        ip = self.guard.client_ip(request)
        if not self.guard.login_allowed(ip):
            raise web.HTTPTooManyRequests(text="Too many login attempts")
        token = request.cookies.get(COOKIE)
        session = self.auth.session(token)
        secret = self.auth.totp_secret()
        if token is None or session is None or not session.pending or secret is None:
            raise web.HTTPUnauthorized(text="login required")
        data = await read_json(request)
        step = totp_match(secret, str(data.get("code", "")))
        if step is None or not self.auth.totp_step_unused("account", step):
            self.guard.login_failed(ip)
            self.app.db.audit(f"web:{ip}", "login.totp_failed")
            raise web.HTTPUnauthorized(text="wrong code")
        full = self.auth.promote(token)
        self.app.db.audit(f"web:{session.username}", "login", ip)
        response = web.json_response({"ok": True})
        self._set_cookie(response, full)
        return response

    async def _logout(self, request: web.Request) -> web.Response:
        token = request.cookies.get(COOKIE)
        if token:
            self.auth.end(token)
        response = web.json_response({"ok": True})
        response.del_cookie(COOKIE, path="/")
        return response
