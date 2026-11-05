"""Request-level protection for the dashboard.

- strict security headers (CSP without inline scripts, no framing, no referrer)
- the site asks not to be indexed (robots.txt and X-Robots-Tag)
- per-IP rate limiting, stricter for login attempts
- temporary bans after repeated failed logins or a honeypot hit
- honeypots: paths scanners probe for, and a hidden login form field
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from aiohttp import web

from pi_guard.security.ratelimit import RateLimiter

log = logging.getLogger(__name__)

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]

HONEYPOT_PATHS = (
    "/wp-login.php", "/wp-admin", "/xmlrpc.php", "/.env", "/.git", "/phpmyadmin",
    "/pma", "/admin.php", "/administrator", "/cgi-bin", "/boaform", "/actuator",
    "/vendor/phpunit", "/server-status", "/config.json", "/.aws",
)  # fmt: skip
TAILNET = ipaddress.ip_network("100.64.0.0/10")
LOGIN_FAILURES = 8
LOGIN_WINDOW = 900.0
TARPIT_SECONDS = 3.0

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob:; connect-src 'self' https://api.ipify.org; font-src 'self'; "
    "manifest-src 'self'; "
    "worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; "
    "object-src 'none'"
)


class Guard:
    def __init__(
        self,
        per_minute: int,
        ban_minutes: int,
        honeypot: bool,
        trusted_proxies: list[str],
        tls: bool,
    ) -> None:
        self._limiter = RateLimiter(per_minute=per_minute, burst=max(30, per_minute // 4))
        self._login_limiter = RateLimiter(per_minute=6, burst=5)
        self._ban_seconds = ban_minutes * 60
        self._honeypot = honeypot
        self._proxies = [ipaddress.ip_network(p, strict=False) for p in trusted_proxies]
        self._tls = tls
        self._bans: dict[str, float] = {}
        self.on_event: Callable[[str, str], None] = lambda action, detail: None
        self.allowed_hosts: set[str] = set()  # empty: any Host header
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def client_ip(self, request: web.Request) -> str:
        peer = request.remote or "unknown"
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded and self._proxies:
            try:
                if any(ipaddress.ip_address(peer) in net for net in self._proxies):
                    return forwarded.split(",")[-1].strip()
            except ValueError:
                pass
        return peer

    def banned(self, ip: str) -> bool:
        until = self._bans.get(ip)
        if until is None:
            return False
        if time.monotonic() > until:
            del self._bans[ip]
            return False
        return True

    def ban(self, ip: str, reason: str) -> None:
        self._bans[ip] = time.monotonic() + self._ban_seconds
        log.warning("dashboard: banned %s for %d min (%s)", ip, self._ban_seconds // 60, reason)
        self.on_event("security.ban", f"{ip}: {reason}")

    def bans(self) -> dict[str, int]:
        now = time.monotonic()
        return {ip: int(until - now) for ip, until in self._bans.items() if until > now}

    def unban(self, ip: str) -> None:
        self._bans.pop(ip, None)

    def _host_allowed(self, host: str) -> bool:
        name = host.rsplit(":", 1)[0].strip("[]").casefold() if host.count(":") <= 1 else host
        if name in self.allowed_hosts:
            return True
        try:
            address = ipaddress.ip_address(name.strip("[]").split("]")[0])
        except ValueError:
            return False
        # Direct access in the home network, or over Tailscale.
        return address.is_private or address.is_loopback or address in TAILNET

    def login_allowed(self, ip: str) -> bool:
        return self._login_limiter.allow(ip)

    def login_failed(self, ip: str) -> None:
        failures = self._failures[ip]
        now = time.monotonic()
        failures.append(now)
        while failures and now - failures[0] > LOGIN_WINDOW:
            failures.popleft()
        if len(failures) >= LOGIN_FAILURES:
            failures.clear()
            self.ban(ip, "too many failed logins")

    def honeypot_hit(self, ip: str, what: str) -> None:
        if self._honeypot:
            self.on_event("security.honeypot", f"{ip}: {what}")
            self.ban(ip, f"honeypot {what}")

    @web.middleware
    async def middleware(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        ip = self.client_ip(request)
        if self.banned(ip):
            raise web.HTTPForbidden(text="Forbidden")
        if self.allowed_hosts and not self._host_allowed(request.host):
            # Unknown names: scanners and DNS rebinding.
            self.on_event("security.bad_host", f"{ip}: {request.host[:80]}")
            raise web.HTTPMisdirectedRequest(text="Unknown host")
        path = request.path.lower()
        if self._honeypot and path.startswith(HONEYPOT_PATHS):
            self.honeypot_hit(ip, request.path[:80])
            await asyncio.sleep(TARPIT_SECONDS)  # costs the scanner time, not us
            raise web.HTTPNotFound()
        # Live streams are long-lived and static files are cheap; neither counts.
        exempt = path.startswith(("/api/live/", "/static/"))
        if not exempt and not self._limiter.allow(ip):
            raise web.HTTPTooManyRequests(text="Too many requests")
        return await handler(request)

    def headers(self, response: web.StreamResponse, request: web.Request) -> None:
        h = response.headers
        h.setdefault("Content-Security-Policy", CSP)
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "no-referrer"
        h["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"
        h["Permissions-Policy"] = "microphone=(self), camera=(self), geolocation=(), payment=()"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        h["Cross-Origin-Resource-Policy"] = "same-origin"
        if self._tls:
            h["Strict-Transport-Security"] = "max-age=31536000"
        # Static files are revalidated, so a fix reaches phones at once instead of after a while.
        static = request.path.startswith("/static/")
        h.setdefault("Cache-Control", "no-cache" if static else "no-store")
