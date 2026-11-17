"""Emergency contacts in the dashboard: who they are and what each gets on which occasion.

Admins only, like everything not opened to viewers. Saving uses the admin session (CSRF);
medical sheet sharing is confirmed in the UI, not by retyping the password every edit.
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
from pi_guard.web.server import current, dashboard, read_json


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
    from pi_guard.storage.persons import Persons

    d = dashboard(request)
    app = d.app
    accounts = [d.auth.account()["username"], *(u["username"] for u in d.auth.users())]
    persons = Persons(app.db)
    contacts = []
    for c in _store(request).all():
        item = c.as_dict()
        linked = persons.find_by_link("emergency_contact", str(c.id))
        item["person_id"] = linked.id if linked else None
        contacts.append(item)
    return web.json_response({
        "accounts": accounts,
        "contacts": contacts,
        "providers": [p for p in PROVIDERS if p in app.providers],
        "all_providers": list(PROVIDERS), "occasions": list(OCCASIONS), "shares": list(SHARES),
        "medical_modes": list(MEDICAL_MODES), "medical": app.addons.medical is not None,
        "after_alerts": app.settings.emergency_after_alerts,
        "after_minutes": app.settings.emergency_after_minutes,
    })  # fmt: skip


async def save(request: web.Request) -> web.Response:
    from pi_guard.storage.persons import Persons

    data = await read_json(request)
    contact_id = int(request.match_info["id"]) if "id" in request.match_info else None
    contact = dict(data.get("contact") or {})
    try:
        saved = _store(request).save(contact_id, contact)
    except ContactError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None
    # Keep the central person list in sync by name (and optional person_id).
    persons = Persons(dashboard(request).app.db)
    person_id = data.get("person_id") or contact.get("person_id")
    if person_id:
        persons.link(int(person_id), "emergency_contact", str(saved))
    else:
        persons.ensure_named(contact.get("name") or "", kind="emergency_contact", ref=str(saved))
    dashboard(request).app.db.audit(_actor(request), "contacts.save", str(saved))
    return web.json_response({"id": saved})


async def delete(request: web.Request) -> web.Response:
    from pi_guard.storage.persons import Persons

    contact_id = int(request.match_info["id"])
    if not _store(request).delete(contact_id):
        raise web.HTTPNotFound()
    Persons(dashboard(request).app.db).unlink("emergency_contact", str(contact_id))
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
