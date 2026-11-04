"""The dashboard account: change login, set up or turn off two-factor codes."""

from __future__ import annotations

import base64
import io

from aiohttp import web

from pi_guard.web.auth import CredentialError, totp_uri
from pi_guard.web.server import SETUP_DONE_KEY, current, dashboard, read_json


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/account", account),
        web.post("/api/account/credentials", credentials),
        web.post("/api/account/totp/begin", totp_begin),
        web.post("/api/account/totp/confirm", totp_confirm),
        web.post("/api/account/totp/disable", totp_disable),
        web.post("/api/setup/done", setup_done),
        web.get("/api/setup/status", setup_status),
    ]


def _actor(request: web.Request) -> str:
    return f"web:{current(request).username}"


async def account(request: web.Request) -> web.Response:
    d = dashboard(request)
    return web.json_response({**d.auth.account(), "methods": sorted(d.settings.dashboard_methods),
                              "setup_done": bool(d.app.db.get(SETUP_DONE_KEY))})  # fmt: skip


async def credentials(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    try:
        d.auth.change_credentials(str(data.get("current", "")), str(data.get("username", "")),
                                  str(data.get("password", "")))  # fmt: skip
    except CredentialError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None
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
    secret = d.auth.begin_totp()
    uri = totp_uri(secret, d.auth.account()["username"])
    return web.json_response({"secret": secret, "uri": uri, "qr": _qr_svg(uri)})


async def totp_confirm(request: web.Request) -> web.Response:
    d = dashboard(request)
    code = str((await read_json(request)).get("code", ""))
    if not d.auth.confirm_totp(code):
        raise web.HTTPBadRequest(text="web.totp_wrong")
    d.app.db.audit(_actor(request), "account.totp_on")
    return web.json_response({"ok": True, "recovery": d.auth.new_recovery_codes()})


async def totp_disable(request: web.Request) -> web.Response:
    d = dashboard(request)
    password = str((await read_json(request)).get("password", ""))
    if not d.auth.disable_totp(password):
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
