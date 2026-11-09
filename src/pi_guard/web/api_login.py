"""More ways to log in: confirmation in Telegram, passkeys and hardware keys, recovery codes.

Telegram: the browser gets a six-digit number and waits; the admin sees the same number in
Telegram together with the requesting address, and confirms. Nothing typed in the browser
can log in by itself, and the wait token never leaves that browser.

WebAuthn needs a host name: browsers refuse it on plain IP addresses like 192.168.178.109.

Login code: pi-guard sends a six-digit code to the owner's admin accounts in one messenger
(Signal, Discord, WhatsApp, the phone app, a chat app, Telegram) or to the account's own email
address, and the code is typed in the browser. Because a messenger or mailbox can be taken
over, this is offered only when a code word or two-factor codes are set, and they are always
asked after it. Five wrong codes end the attempt.
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

from pi_guard.core.permissions import Role
from pi_guard.core.timeutil import utcnow
from pi_guard.messaging.base import Button, Reply
from pi_guard.web.auth import PENDING_CODEWORD, PENDING_NONE, CredentialError
from pi_guard.web.server import confirm_password, current, dashboard, read_json

log = logging.getLogger(__name__)

REQUEST_SECONDS = 120
MAX_OPEN_REQUESTS = 5
CHALLENGE_SECONDS = 180
CODE_SECONDS = 300
CODE_TRIES = 5


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
class _Code:
    digest: str
    channel: str
    created: float
    tries: int = 0


@dataclass
class _Challenge:
    value: bytes
    created: float
    purpose: str


class LoginFlows:
    """Short-lived state of logins in progress. Kept in memory: a restart simply cancels them."""

    def __init__(self) -> None:
        self.telegram: dict[str, _Telegram] = {}
        self.codes: dict[str, _Code] = {}
        self.questions: dict[str, int] = {}  # hash of a waiting login -> the question it got
        self.challenges: dict[str, _Challenge] = {}

    def expire(self) -> None:
        now = time.monotonic()
        self.telegram = {k: v for k, v in self.telegram.items()
                         if now - v.created < REQUEST_SECONDS}  # fmt: skip
        self.challenges = {k: v for k, v in self.challenges.items()
                           if now - v.created < CHALLENGE_SECONDS}  # fmt: skip
        self.codes = {k: v for k, v in self.codes.items() if now - v.created < CODE_SECONDS}

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
        web.get("/api/login/channels", code_channels),
        web.post("/api/login/pgp/begin", pgp_begin),
        web.post("/api/login/pgp/finish", pgp_finish),
        web.get("/api/account/pgp", pgp_list),
        web.post("/api/account/pgp", pgp_add),
        web.delete("/api/account/pgp/{fingerprint:[0-9A-F]{40,64}}", pgp_delete),
        web.post("/api/login/code", code_send),
        web.post("/api/login/code/verify", code_verify),
        web.post("/api/login/webauthn/begin", webauthn_login_begin),
        web.post("/api/login/webauthn/finish", webauthn_login_finish),
        web.get("/api/account/webauthn", webauthn_list),
        web.post("/api/account/webauthn/begin", webauthn_register_begin),
        web.post("/api/account/webauthn/finish", webauthn_register_finish),
        web.delete("/api/account/webauthn/{id}", webauthn_delete),
        web.post("/api/account/recovery", recovery_codes),
        web.post("/api/account/codeword", codeword),
        web.get("/api/account/questions", questions_get),
        web.post("/api/account/questions", question_add),
        web.delete("/api/account/questions/{id:\\d+}", question_delete),
    ]


def _start_session(request: web.Request, how: str, second: bool = False) -> web.Response:
    """Telegram and passkeys replace password and TOTP; a code word is still asked for.
    With `second`, two-factor codes are asked as well."""
    d = dashboard(request)
    username = d.auth.account()["username"]
    ip = d.guard.client_ip(request)
    pending = PENDING_CODEWORD if d.auth.has_knowledge_step else PENDING_NONE
    if second:
        pending = d._second_step(username)
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


# --- login code by messenger or email ------------------------------------------------------


def _code_targets(d: Any) -> dict[str, list[str]]:
    """Channel -> where the code goes. Only the owner's own ways: admins of a messenger, or
    the email address in the account's contact details."""
    app = d.app
    owner = d.auth.account()["username"]
    if not (d.auth.has_codeword or d.auth.totp_secret(owner)):
        return {}  # no second step that a stolen mailbox would not pass
    targets: dict[str, list[str]] = {}
    for name in app.providers:
        if name == "email":
            address = d.profiles.get(owner).get("email")
            if address:
                targets["email"] = [address]
            continue
        admins = [u.user_id for u in app.users.all(name) if u.role is Role.ADMIN]
        if admins:
            targets[name] = admins
    return targets


async def code_channels(request: web.Request) -> web.Response:
    d = dashboard(request)
    if "code" not in d.login_methods():
        return web.json_response([])
    return web.json_response(sorted(_code_targets(d)))


async def code_send(request: web.Request) -> web.Response:
    d = dashboard(request)
    ip = d.guard.client_ip(request)
    if "code" not in d.login_methods():
        raise web.HTTPNotFound()
    d.logins.expire()
    if not d.guard.login_allowed(ip) or len(d.logins.codes) >= MAX_OPEN_REQUESTS:
        raise web.HTTPTooManyRequests(text="Too many login attempts")
    channel = str((await read_json(request)).get("channel", ""))
    targets = _code_targets(d).get(channel)
    if not targets:
        raise web.HTTPBadRequest(text="web.code_no_channel")
    from pi_guard.i18n import t

    code = f"{secrets.randbelow(10**6):06d}"
    lang = d.app.settings.language
    text = t("web.code_message", lang, code=code, ip=ip,
             agent=request.headers.get("User-Agent", "")[:80])  # fmt: skip
    sent = 0
    for target in targets:
        try:
            await d.app.messenger.send(channel, target, Reply(text))
            sent += 1
        except Exception as exc:
            log.warning("login code not delivered: %s", type(exc).__name__)
    if not sent:
        raise web.HTTPServiceUnavailable(text="web.code_not_delivered")
    request_id = secrets.token_urlsafe(16)
    d.logins.codes[request_id] = _Code(_hash_code(request_id, code), channel, time.monotonic())
    d.app.db.audit(f"web:{ip}", "login.code_sent", channel)
    return web.json_response({"id": request_id, "expires": CODE_SECONDS})


def _hash_code(request_id: str, code: str) -> str:
    import hashlib

    return hashlib.sha256(f"{request_id}:{code}".encode()).hexdigest()


async def code_verify(request: web.Request) -> web.Response:
    d = dashboard(request)
    ip = d.guard.client_ip(request)
    if not d.guard.login_allowed(ip):
        raise web.HTTPTooManyRequests(text="Too many login attempts")
    data = await read_json(request)
    request_id, code = str(data.get("id", "")), str(data.get("code", "")).replace(" ", "")
    d.logins.expire()
    pending = d.logins.codes.get(request_id)
    if pending is None:
        raise web.HTTPGone(text="web.code_expired")
    if not secrets.compare_digest(pending.digest, _hash_code(request_id, code)):
        pending.tries += 1
        d.guard.login_failed(ip)
        if pending.tries >= CODE_TRIES:
            del d.logins.codes[request_id]
        raise web.HTTPUnauthorized(text="web.code_wrong")
    del d.logins.codes[request_id]
    return _start_session(request, f"code.{pending.channel}", second=True)


# --- OpenPGP ------------------------------------------------------------------------------


def _pgp(request: web.Request) -> Any:
    from pi_guard.web.pgp import PgpKeys

    return PgpKeys(dashboard(request).app.db)


def _password_ok(request: web.Request, data: dict[str, Any]) -> None:
    d = dashboard(request)
    if d.auth.check_password(d.auth.account()["username"], str(data.get("password", ""))) is None:
        raise web.HTTPForbidden(text="web.wrong_password")


async def pgp_list(request: web.Request) -> web.Response:
    from pi_guard.web.pgp import available

    fields = ("fingerprint", "name", "created")
    keys = [{k: key[k] for k in fields} for key in _pgp(request).entries()]
    return web.json_response({"keys": keys, "available": available()})


async def pgp_add(request: web.Request) -> web.Response:
    from pi_guard.web.pgp import PgpError

    data = await read_json(request)
    _password_ok(request, data)
    try:
        added = _pgp(request).add(str(data.get("key", "")))
    except PgpError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None
    dashboard(request).app.db.audit(f"web:{current(request).username}", "account.pgp_add",
                                    added["fingerprint"][-16:])  # fmt: skip
    return web.json_response(added)


async def pgp_delete(request: web.Request) -> web.Response:
    data = await read_json(request)
    _password_ok(request, data)
    if not _pgp(request).delete(request.match_info["fingerprint"]):
        raise web.HTTPNotFound()
    dashboard(request).app.db.audit(f"web:{current(request).username}", "account.pgp_remove")
    return web.json_response({"ok": True})


async def pgp_begin(request: web.Request) -> web.Response:
    d = dashboard(request)
    ip = d.guard.client_ip(request)
    if "pgp" not in d.login_methods():
        raise web.HTTPNotFound()
    d.logins.expire()
    if not d.guard.login_allowed(ip) or len(d.logins.challenges) >= 50:
        raise web.HTTPTooManyRequests(text="Too many login attempts")
    text = (f"pi-guard login\nhost: {request.host[:80]}\nnonce: {secrets.token_hex(16)}\n"
            f"time: {utcnow().isoformat(timespec='seconds')}")  # fmt: skip
    flow = secrets.token_urlsafe(16)
    d.logins.challenges[flow] = _Challenge(text.encode(), time.monotonic(), "pgp")
    return web.json_response({"flow": flow, "text": text})


async def pgp_finish(request: web.Request) -> web.Response:
    import asyncio

    from pi_guard.web.pgp import verify

    d = dashboard(request)
    ip = d.guard.client_ip(request)
    if not d.guard.login_allowed(ip):
        raise web.HTTPTooManyRequests(text="Too many login attempts")
    data = await read_json(request)
    d.logins.expire()
    challenge = d.logins.challenges.pop(str(data.get("flow", "")), None)
    if challenge is None or challenge.purpose != "pgp":
        raise web.HTTPGone(text="web.pgp_expired")
    keys = _pgp(request)
    signer = await asyncio.to_thread(verify, keys.armored(), str(data.get("signed", "")),
                                     challenge.value.decode())  # fmt: skip
    known = {k["fingerprint"] for k in keys.entries()}
    if signer is None or signer not in known:
        d.guard.login_failed(ip)
        d.app.db.audit(f"web:{ip}", "login.failed", "pgp")
        raise web.HTTPUnauthorized(text="web.pgp_wrong")
    return _start_session(request, "pgp")


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


async def questions_get(request: web.Request) -> web.Response:
    return web.json_response(dashboard(request).auth.questions())


async def question_add(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    try:
        d.auth.add_question(str(data.get("password", "")), data)
    except CredentialError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None
    d.app.db.audit(f"web:{current(request).username}", "account.question_add")
    return web.json_response(d.auth.questions())


async def question_delete(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    try:
        found = d.auth.delete_question(str(data.get("password", "")), int(request.match_info["id"]))
    except CredentialError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None
    if not found:
        raise web.HTTPNotFound()
    d.app.db.audit(f"web:{current(request).username}", "account.question_delete")
    return web.json_response(d.auth.questions())


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
