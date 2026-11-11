"""Emergency contacts in the dashboard: who they are and what each gets on which occasion.

Admins only, like everything not opened to viewers. Saving asks for the password, because a
contact can be set to get pictures, the place of a phone and emergency medical data.
Contacts from the older settings are listed, but changed in the settings only.
"""

from __future__ import annotations

from aiohttp import web

from pi_guard.i18n import t
from pi_guard.messaging.base import ProviderUnavailableError, Reply
from pi_guard.notifications.contacts import (
    MEDICAL_MODES,
    OCCASIONS,
    PROVIDERS,
    SHARES,
    ContactError,
    Contacts,
)
from pi_guard.web.server import confirm_password, current, dashboard, read_json


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/contacts", listing),
        web.post("/api/contacts", save),
        web.put("/api/contacts/{id:\\d+}", save),
        web.delete("/api/contacts/{id:\\d+}", delete),
        web.post("/api/contacts/{id:-?\\d+}/test", test),
    ]


def _store(request: web.Request) -> Contacts:
    return dashboard(request).app.escalation.contacts


def _actor(request: web.Request) -> str:
    return f"web:{current(request).username}"


async def listing(request: web.Request) -> web.Response:
    d = dashboard(request)
    app = d.app
    accounts = [d.auth.account()["username"], *(u["username"] for u in d.auth.users())]
    return web.json_response({
        "accounts": accounts,
        "contacts": [c.as_dict() for c in _store(request).all()],
        "providers": [p for p in PROVIDERS if p in app.providers],
        "all_providers": list(PROVIDERS), "occasions": list(OCCASIONS), "shares": list(SHARES),
        "medical_modes": list(MEDICAL_MODES), "medical": app.addons.medical is not None,
        "after_alerts": app.settings.emergency_after_alerts,
        "after_minutes": app.settings.emergency_after_minutes,
    })  # fmt: skip


async def save(request: web.Request) -> web.Response:
    data = await read_json(request)
    confirm_password(request, str(data.get("password", "")))
    contact_id = int(request.match_info["id"]) if "id" in request.match_info else None
    try:
        saved = _store(request).save(contact_id, dict(data.get("contact") or {}))
    except ContactError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None
    dashboard(request).app.db.audit(_actor(request), "contacts.save", str(saved))
    return web.json_response({"id": saved})


async def delete(request: web.Request) -> web.Response:
    contact_id = int(request.match_info["id"])
    if not _store(request).delete(contact_id):
        raise web.HTTPNotFound()
    dashboard(request).app.db.audit(_actor(request), "contacts.delete", str(contact_id))
    return web.json_response({"ok": True})


async def test(request: web.Request) -> web.Response:
    """A short message, so the owner knows the contact can be reached before it matters."""
    app = dashboard(request).app
    contact = _store(request).get(int(request.match_info["id"]))
    if contact is None:
        raise web.HTTPNotFound()
    lang = app.settings.language
    results = []
    for provider, address in contact.channels:
        ok = provider in app.providers
        if ok:
            try:
                reply = Reply(t("emergency.test", lang, name=contact.name))
                await app.messenger.send(provider, address, reply)
            except (ProviderUnavailableError, RuntimeError, OSError):
                ok = False
        results.append({"provider": provider, "ok": ok})
    app.db.audit(_actor(request), "contacts.test", str(contact.id))
    return web.json_response({"results": results})
