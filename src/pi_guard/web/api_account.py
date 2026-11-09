"""The dashboard account: change login, set up or turn off two-factor codes."""

from __future__ import annotations

import base64
import io
import re

from aiohttp import web

from pi_guard.security.files import atomic_write
from pi_guard.web.api_mobile import forget_devices, role_changed
from pi_guard.web.auth import CredentialError, totp_uri
from pi_guard.web.profiles import ProfileError, clean, from_vcard, to_vcard
from pi_guard.web.server import COOKIE, SETUP_DONE_KEY, current, dashboard, read_json


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/account", account),
        web.post("/api/account/credentials", credentials),
        web.post("/api/account/totp/begin", totp_begin),
        web.post("/api/account/totp/confirm", totp_confirm),
        web.post("/api/account/totp/disable", totp_disable),
        web.post("/api/setup/done", setup_done),
        web.post("/api/me/password", my_password),
        web.get("/api/me/profile", my_profile),
        web.put("/api/me/profile", my_profile_save),
        web.get("/api/me/profile/vcard", my_vcard),
        web.post("/api/me/profile/vcard", read_vcard),
        web.get("/api/profiles/{name}", profile_get),
        web.put("/api/profiles/{name}", profile_save),
        web.get("/api/profiles/{name}/vcard", profile_vcard),
        web.get("/api/dashboard-users", users),
        web.post("/api/dashboard-users", user_add),
        web.patch("/api/dashboard-users/{id:\\d+}", user_role),
        web.delete("/api/dashboard-users/{id:\\d+}", user_delete),
        web.put("/api/appearance", appearance),
        web.post("/api/appearance/logo", logo_upload),
        web.delete("/api/appearance/logo", logo_delete),
        web.get("/api/setup/status", setup_status),
    ]


def _actor(request: web.Request) -> str:
    return f"web:{current(request).username}"


async def account(request: web.Request) -> web.Response:
    d = dashboard(request)
    me = current(request).username
    return web.json_response({**d.auth.account(me), "methods": sorted(d.settings.dashboard_methods),
                              "setup_done": bool(d.app.db.get(SETUP_DONE_KEY))})  # fmt: skip


async def credentials(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    try:
        d.auth.change_credentials(str(data.get("current", "")), str(data.get("username", "")),
                                  str(data.get("password", "")))  # fmt: skip
    except CredentialError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None
    old = current(request).username
    new = d.auth.account()["username"]
    d.profiles.rename(old, new)  # the contact details move with the account
    d.auth.end_other_sessions(new, request.cookies.get(COOKIE))
    d.app.db.audit(_actor(request), "account.credentials")
    return web.json_response({"ok": True, "username": d.auth.account()["username"]})


def _qr_svg(text: str) -> str:
    import qrcode
    import qrcode.image.svg

    image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    buffer = io.BytesIO()
    image.save(buffer)
    return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode()


async def totp_begin(request: web.Request) -> web.Response:
    d = dashboard(request)
    me = current(request).username
    secret = d.auth.begin_totp(me)
    uri = totp_uri(secret, d.auth.account(me)["username"])
    return web.json_response({"secret": secret, "uri": uri, "qr": _qr_svg(uri)})


async def totp_confirm(request: web.Request) -> web.Response:
    d = dashboard(request)
    code = str((await read_json(request)).get("code", ""))
    me = current(request).username
    if not d.auth.confirm_totp(code, me):
        raise web.HTTPBadRequest(text="web.totp_wrong")
    d.app.db.audit(_actor(request), "account.totp_on")
    codes = d.auth.new_recovery_codes() if d.auth.is_owner(me) else []
    return web.json_response({"ok": True, "recovery": codes})


async def totp_disable(request: web.Request) -> web.Response:
    d = dashboard(request)
    password = str((await read_json(request)).get("password", ""))
    if not d.auth.disable_totp(password, current(request).username):
        raise web.HTTPBadRequest(text="web.wrong_password")
    d.app.db.audit(_actor(request), "account.totp_off")
    return web.json_response({"ok": True})


async def setup_done(request: web.Request) -> web.Response:
    d = dashboard(request)
    d.app.db.set(SETUP_DONE_KEY, True)
    d.app.db.audit(_actor(request), "setup.done")
    return web.json_response({"ok": True})


async def setup_status(request: web.Request) -> web.Response:
    """What the installer (or an earlier run of the assistant) already set up.

    The assistant shows these steps as done with a short summary instead of asking again.
    """
    app = dashboard(request).app
    s = app.settings
    configured = s.model_fields_set
    telegram = app.providers.get("telegram")
    file = app.config.file
    return web.json_response({
        "language": s.language if "language" in configured else None,
        "telegram": {
            "bot": getattr(telegram, "username", None),
            "admins": len(s.telegram_admin_user_ids),
        } if s.telegram_ready and s.telegram_admin_user_ids else None,
        "devices": {
            "cameras": [c.name for c in file.cameras if c.enabled],
            "speaker": file.audio.speaker if s.audio_playback_enabled else None,
            "microphone": file.audio.microphone if s.audio_recording_enabled else None,
        } if file.cameras else None,
        "presence": s.presence_enabled if "presence_enabled" in configured else None,
    })  # fmt: skip


# --- more people ----------------------------------------------------------------------------


async def my_password(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    try:
        d.auth.change_own_password(current(request).username, str(data.get("current", "")),
                                   str(data.get("password", "")))  # fmt: skip
    except CredentialError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None
    d.auth.end_other_sessions(current(request).username, request.cookies.get(COOKIE))
    return web.json_response({"ok": True})


async def users(request: web.Request) -> web.Response:
    d = dashboard(request)
    return web.json_response({"owner": d.auth.account()["username"], "users": d.auth.users()})


async def user_add(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    try:
        d.auth.add_user(str(data.get("username", "")), str(data.get("password", "")),
                        str(data.get("role", "viewer")))  # fmt: skip
    except CredentialError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None
    d.app.db.audit(_actor(request), "account.user_add", str(data.get("username", ""))[:32])
    return web.json_response({"ok": True})


async def user_role(request: web.Request) -> web.Response:
    d = dashboard(request)
    role = str((await read_json(request)).get("role", ""))
    user_id = int(request.match_info["id"])
    if not d.auth.set_role(user_id, role):
        raise web.HTTPBadRequest(text="web.error")
    await role_changed(
        request, next(u["username"] for u in d.auth.users() if u["id"] == user_id), role
    )
    d.app.db.audit(_actor(request), "account.user_role", role)
    return web.json_response({"ok": True})


async def user_delete(request: web.Request) -> web.Response:
    d = dashboard(request)
    username = d.auth.delete_user(int(request.match_info["id"]))
    if username is None:
        raise web.HTTPNotFound()
    devices = await forget_devices(request, username)
    d.profiles.delete(username)
    d.app.db.audit(_actor(request), "account.user_remove", f"{devices} device(s)")
    return web.json_response({"ok": True})


# --- contact details ------------------------------------------------------------------------


def _profile_name(request: web.Request) -> str:
    d = dashboard(request)
    name = request.match_info["name"]
    special = name == "local" or name.startswith(("key:", "api:"))  # logins, not people
    if special or not d.auth.exists(name):
        raise web.HTTPNotFound()
    return d.auth.account(name)["username"] if d.auth.is_owner(name) else name


async def _save(request: web.Request, username: str) -> web.Response:
    d = dashboard(request)
    try:
        values = d.profiles.set(username, await read_json(request))
    except ProfileError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None
    d.app.db.audit(_actor(request), "account.profile", username)
    return web.json_response(values)


def _vcard_response(username: str, data: dict[str, str]) -> web.Response:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", username)[:40] or "contact"
    return web.Response(
        text=to_vcard(username, data),
        content_type="text/vcard",
        headers={
            "Content-Disposition": f'attachment; filename="{safe}.vcf"',
            "Cache-Control": "no-store",
        },
    )


async def my_profile(request: web.Request) -> web.Response:
    return web.json_response(dashboard(request).profiles.get(current(request).username))


async def my_profile_save(request: web.Request) -> web.Response:
    return await _save(request, current(request).username)


async def my_vcard(request: web.Request) -> web.Response:
    name = current(request).username
    return _vcard_response(name, dashboard(request).profiles.get(name))


async def read_vcard(request: web.Request) -> web.Response:
    """The fields of an uploaded card, checked but not stored: the page fills its form."""
    text = (await request.content.read(64 * 1024 + 1)).decode("utf-8", errors="replace")
    try:
        return web.json_response(clean(from_vcard(text)))
    except ProfileError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None


async def profile_get(request: web.Request) -> web.Response:
    return web.json_response(dashboard(request).profiles.get(_profile_name(request)))


async def profile_save(request: web.Request) -> web.Response:
    return await _save(request, _profile_name(request))


async def profile_vcard(request: web.Request) -> web.Response:
    name = _profile_name(request)
    return _vcard_response(name, dashboard(request).profiles.get(name))


# --- appearance -----------------------------------------------------------------------------

FONTS = ("system", "serif", "rounded", "mono", "readable")
MAX_APP_NAME = 30
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
LOGO_TYPES = {
    b"\x89PNG": "png",
    b"\xff\xd8\xff": "jpg",
    b"RIFF": "webp",
}  # no SVG: it can hold scripts
MAX_LOGO = 1_000_000


def _logo_type(body: bytes) -> str | None:
    """Pictures only, recognised by content. No SVG: it can hold scripts."""
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if body.startswith(b"\xff\xd8\xff"):
        return "jpg"
    return "webp" if body[:4] == b"RIFF" and body[8:12] == b"WEBP" else None


async def appearance(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    accent, font = str(data.get("accent", "")), str(data.get("font", "system"))
    if accent and not _COLOR_RE.match(accent):
        raise web.HTTPBadRequest(text="web.error")
    if font not in FONTS:
        raise web.HTTPBadRequest(text="web.error")
    name = " ".join(str(data.get("name", "")).split())[:MAX_APP_NAME]
    saved = d.app.db.get("web.appearance") or {}
    d.app.db.set("web.appearance", {**saved, "accent": accent, "font": font, "name": name,
                                    "animations": data.get("animations") is not False})  # fmt: skip
    return web.json_response({"ok": True})


async def logo_upload(request: web.Request) -> web.Response:
    d = dashboard(request)
    body = await request.read()
    kind = _logo_type(body)
    if kind is None or len(body) > MAX_LOGO:
        raise web.HTTPBadRequest(text="web.ap_bad_logo")
    for old in d.settings.data_dir.glob("logo.*"):
        old.unlink()
    atomic_write(d.settings.data_dir / f"logo.{kind}", body, 0o644)
    saved = d.app.db.get("web.appearance") or {}
    d.app.db.set("web.appearance", {**saved, "logo": True})
    return web.json_response({"ok": True})


async def logo_delete(request: web.Request) -> web.Response:
    d = dashboard(request)
    for old in d.settings.data_dir.glob("logo.*"):
        old.unlink()
    saved = d.app.db.get("web.appearance") or {}
    d.app.db.set("web.appearance", {**saved, "logo": False})
    return web.json_response({"ok": True})
