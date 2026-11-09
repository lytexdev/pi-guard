"""More ways to log in: confirmation in Telegram, passkeys and hardware keys, recovery codes.

Telegram: the browser gets a six-digit number and waits; the admin sees the same number in
Telegram together with the requesting address, and confirms. Nothing typed in the browser
can log in by itself, and the wait token never leaves that browser.

WebAuthn needs a host name: browsers refuse it on plain IP addresses like 192.168.178.109.
"""

from __future__ import annotations

import base64
import logging
import secrets
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web

from pi_guard.messaging.base import Button, Reply
from pi_guard.web.auth import PENDING_CODEWORD, PENDING_NONE, CredentialError
from pi_guard.web.server import confirm_password, current, dashboard, read_json

log = logging.getLogger(__name__)

REQUEST_SECONDS = 120
MAX_OPEN_REQUESTS = 5
CHALLENGE_SECONDS = 180


@dataclass
class _Telegram:
    number: str
    poll: str
    ip: str
    agent: str
    created: float
    approved: bool = False
    denied: bool = False


@dataclass
class _Challenge:
    value: bytes
    created: float
    purpose: str


class LoginFlows:
    """Short-lived state of logins in progress. Kept in memory: a restart simply cancels them."""

    def __init__(self) -> None:
        self.telegram: dict[str, _Telegram] = {}
        self.challenges: dict[str, _Challenge] = {}

    def expire(self) -> None:
        now = time.monotonic()
        self.telegram = {k: v for k, v in self.telegram.items()
                         if now - v.created < REQUEST_SECONDS}  # fmt: skip
        self.challenges = {k: v for k, v in self.challenges.items()
                           if now - v.created < CHALLENGE_SECONDS}  # fmt: skip

    def answer(self, request_id: str, approve: bool) -> _Telegram | None:
        self.expire()
        pending = self.telegram.get(request_id)
        if pending is not None:
            pending.approved, pending.denied = approve, not approve
        return pending


def routes() -> list[web.RouteDef]:
    return [
        web.post("/api/login/telegram", telegram_start),
        web.post("/api/login/telegram/poll", telegram_poll),
        web.post("/api/login/webauthn/begin", webauthn_login_begin),
        web.post("/api/login/webauthn/finish", webauthn_login_finish),
        web.get("/api/account/webauthn", webauthn_list),
        web.post("/api/account/webauthn/begin", webauthn_register_begin),
        web.post("/api/account/webauthn/finish", webauthn_register_finish),
        web.delete("/api/account/webauthn/{id}", webauthn_delete),
        web.post("/api/account/recovery", recovery_codes),
        web.post("/api/account/codeword", codeword),
    ]


def _start_session(request: web.Request, how: str) -> web.Response:
    """Telegram and passkeys replace password and TOTP; a code word is still asked for."""
    d = dashboard(request)
    username = d.auth.account()["username"]
    ip = d.guard.client_ip(request)
    pending = PENDING_CODEWORD if d.auth.has_codeword else PENDING_NONE
    token = d.auth.create_session(username, ip, request.headers.get("User-Agent", ""), pending)
    if not pending:
        d.app.db.audit(f"web:{username}", f"login.{how}", ip)
    response = web.json_response({"ok": True, "totp_required": bool(pending)})
    d.set_cookie(response, token)
    return response


# --- Telegram -----------------------------------------------------------------------------


async def telegram_start(request: web.Request) -> web.Response:
    d = dashboard(request)
    ip = d.guard.client_ip(request)
    if "telegram" not in d.login_methods():
        raise web.HTTPNotFound()
    d.logins.expire()
    if not d.guard.login_allowed(ip) or len(d.logins.telegram) >= MAX_OPEN_REQUESTS:
        raise web.HTTPTooManyRequests(text="Too many login attempts")
    request_id = secrets.token_hex(8)
    pending = _Telegram(f"{secrets.randbelow(10**6):06d}", secrets.token_urlsafe(32), ip,
                        request.headers.get("User-Agent", "")[:80], time.monotonic())  # fmt: skip
    d.logins.telegram[request_id] = pending
    app = d.app
    from pi_guard.i18n import t

    lang = app.settings.language
    text = t("web.tg_login_message", lang, number=pending.number, ip=ip, agent=pending.agent)
    buttons = (
        (Button(t("web.tg_login_yes", lang), f"weblogin yes {request_id}"),),
        (Button(t("web.tg_login_no", lang), f"weblogin no {request_id}"),),
    )
    sent = 0
    for admin in app.settings.telegram_admin_user_ids:
        try:
            await app.messenger.send("telegram", str(admin), Reply(text, buttons=buttons))
            sent += 1
        except Exception as exc:
            log.warning("could not reach an admin for a login: %s", type(exc).__name__)
    if not sent:
        raise web.HTTPServiceUnavailable(text="web.tg_login_unavailable")
    return web.json_response({"number": pending.number, "poll": pending.poll,
                              "expires": REQUEST_SECONDS})  # fmt: skip


async def telegram_poll(request: web.Request) -> web.Response:
    d = dashboard(request)
    poll = str((await read_json(request)).get("poll", ""))
    d.logins.expire()
    for request_id, pending in d.logins.telegram.items():
        if secrets.compare_digest(pending.poll, poll):
            if pending.denied:
                del d.logins.telegram[request_id]
                return web.json_response({"state": "denied"})
            if pending.approved:
                del d.logins.telegram[request_id]
                return _start_session(request, "telegram")
            return web.json_response({"state": "waiting"})
    return web.json_response({"state": "expired"})


# --- WebAuthn -----------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _relying_party(request: web.Request) -> tuple[str, str]:
    """(RP ID, origin) as the browser sees this page."""
    d = dashboard(request)
    public = d.settings.dashboard_public_url
    host = urlsplit(f"//{request.host}").hostname or ""
    if public and urlsplit(public).hostname == host:
        return host, public
    if not host or host.replace(".", "").isdigit() or ":" in host:
        raise web.HTTPConflict(text="web.webauthn_needs_name")
    scheme = "https" if d.settings.dashboard_tls else request.scheme
    return host, f"{scheme}://{request.host}"


def _remember(request: web.Request, challenge: bytes, purpose: str) -> str:
    d = dashboard(request)
    d.logins.expire()
    flow = secrets.token_urlsafe(24)
    d.logins.challenges[flow] = _Challenge(challenge, time.monotonic(), purpose)
    return flow


def _take(request: web.Request, flow: str, purpose: str) -> bytes:
    """Anyone can get a login challenge, so it must not open the way to adding a key."""
    d = dashboard(request)
    d.logins.expire()
    found = d.logins.challenges.pop(flow, None)
    if found is None or found.purpose != purpose:
        raise web.HTTPUnauthorized(text="login failed")
    return found.value


async def webauthn_list(request: web.Request) -> web.Response:
    d = dashboard(request)
    keys = [{"id": c["id"], "name": c["name"], "created": c["created"], "last_used": c["last_used"]}
            for c in d.auth.credentials()]  # fmt: skip
    try:
        _relying_party(request)
        usable = True
    except web.HTTPConflict:
        usable = False
    names = [socket.gethostname(), *d.settings.dashboard_tls_names]  # the certificate has both
    names = [n for n in dict.fromkeys(names) if ":" not in n and not n.replace(".", "").isdigit()]
    return web.json_response({"keys": keys, "usable_here": usable, "names": names,
                              "port": d.settings.dashboard_port})  # fmt: skip


async def webauthn_register_begin(request: web.Request) -> web.Response:
    import json

    import webauthn
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    d = dashboard(request)
    rp_id, _ = _relying_party(request)
    confirm_password(request, str((await read_json(request)).get("password", "")), owner=True)
    options = webauthn.generate_registration_options(
        rp_id=rp_id, rp_name="pi-guard", user_name=d.auth.account()["username"],
        user_id=b"pi-guard-account",
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=_unb64(c["id"]))
                             for c in d.auth.credentials()],
    )  # fmt: skip
    flow = _remember(request, options.challenge, "register")
    return web.json_response(
        {"flow": flow, "options": json.loads(webauthn.options_to_json(options))}
    )


async def webauthn_register_finish(request: web.Request) -> web.Response:
    import webauthn
    from webauthn.helpers.exceptions import WebAuthnException

    d = dashboard(request)
    data = await read_json(request)
    rp_id, origin = _relying_party(request)
    challenge = _take(request, str(data.get("flow", "")), "register")
    credential: Any = data.get("credential")
    try:
        verified = webauthn.verify_registration_response(
            credential=credential, expected_challenge=challenge,
            expected_rp_id=rp_id, expected_origin=origin,
        )  # fmt: skip
    except (WebAuthnException, ValueError, KeyError, TypeError):
        raise web.HTTPBadRequest(text="web.webauthn_failed") from None
    name = str(data.get("name") or "Key").strip() or "Key"
    d.auth.add_credential(_b64(verified.credential_id), _b64(verified.credential_public_key),
                          verified.sign_count, name)  # fmt: skip
    d.app.db.audit(f"web:{current(request).username}", "account.webauthn_add", name[:40])
    return web.json_response({"ok": True})


async def webauthn_delete(request: web.Request) -> web.Response:
    d = dashboard(request)
    if not d.auth.delete_credential(request.match_info["id"]):
        raise web.HTTPNotFound()
    d.app.db.audit(f"web:{current(request).username}", "account.webauthn_remove")
    return web.json_response({"ok": True})


async def webauthn_login_begin(request: web.Request) -> web.Response:
    import json

    import webauthn
    from webauthn.helpers.structs import PublicKeyCredentialDescriptor

    d = dashboard(request)
    ip = d.guard.client_ip(request)
    if not d.guard.login_allowed(ip):
        raise web.HTTPTooManyRequests(text="Too many login attempts")
    rp_id, _ = _relying_party(request)
    options = webauthn.generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[PublicKeyCredentialDescriptor(id=_unb64(c["id"]))
                           for c in d.auth.credentials()],
    )  # fmt: skip
    flow = _remember(request, options.challenge, "login")
    return web.json_response(
        {"flow": flow, "options": json.loads(webauthn.options_to_json(options))}
    )


async def webauthn_login_finish(request: web.Request) -> web.Response:
    import webauthn
    from webauthn.helpers.exceptions import WebAuthnException

    d = dashboard(request)
    ip = d.guard.client_ip(request)
    data = await read_json(request)
    rp_id, origin = _relying_party(request)
    challenge = _take(request, str(data.get("flow", "")), "login")
    credential: Any = data.get("credential")
    wanted = credential.get("id") if isinstance(credential, dict) else None
    stored = next((c for c in d.auth.credentials() if c["id"] == wanted), None)
    if stored is None:
        d.guard.login_failed(ip)
        raise web.HTTPUnauthorized(text="login failed")
    try:
        verified = webauthn.verify_authentication_response(
            credential=credential, expected_challenge=challenge, expected_rp_id=rp_id,
            expected_origin=origin, credential_public_key=_unb64(stored["public_key"]),
            credential_current_sign_count=stored["sign_count"],
        )  # fmt: skip
    except (WebAuthnException, ValueError, KeyError, TypeError):
        d.guard.login_failed(ip)
        d.app.db.audit(f"web:{ip}", "login.failed", "webauthn")
        raise web.HTTPUnauthorized(text="login failed") from None
    d.auth.credential_used(stored["id"], verified.new_sign_count)
    return _start_session(request, "webauthn")


# --- recovery codes and code word ---------------------------------------------------------


async def recovery_codes(request: web.Request) -> web.Response:
    d = dashboard(request)
    password = str((await read_json(request)).get("password", ""))
    account = d.auth.account()
    if not account["totp"]:
        raise web.HTTPConflict(text="web.recovery_needs_totp")
    if d.auth.check_password(account["username"], password) is None:
        raise web.HTTPBadRequest(text="web.wrong_password")
    d.app.db.audit(f"web:{current(request).username}", "account.recovery_codes")
    return web.json_response({"codes": d.auth.new_recovery_codes()})


async def codeword(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    try:
        d.auth.set_codeword(str(data.get("password", "")), str(data.get("codeword", "")),
                            str(data.get("hint") or ""))  # fmt: skip
    except CredentialError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None
    d.app.db.audit(f"web:{current(request).username}", "account.codeword")
    return web.json_response({"ok": True})
