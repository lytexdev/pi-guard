"""Central person records for the dashboard (faces, accounts, messengers, emergency)."""

from __future__ import annotations

from aiohttp import web

from pi_guard.storage.persons import KINDS, ROLES, PersonError, Persons
from pi_guard.web.server import confirm_password, current, dashboard, read_json


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/persons", listing),
        web.post("/api/persons", save),
        web.put("/api/persons/{id:\\d+}", save),
        web.delete("/api/persons/{id:\\d+}", delete),
        web.post("/api/persons/migrate", migrate),
    ]


def _store(request: web.Request) -> Persons:
    return Persons(dashboard(request).app.db)


def _actor(request: web.Request) -> str:
    return f"web:{current(request).username}"


async def listing(request: web.Request) -> web.Response:
    d = dashboard(request)
    store = _store(request)
    faces = [name for name, *_ in d.app.faces.store.people()] if d.app.faces is not None else []
    accounts = [d.auth.account()["username"], *(u["username"] for u in d.auth.users())]
    contacts = [c.as_dict() for c in d.app.escalation.contacts.all()] if d.app.escalation else []
    return web.json_response({
        "persons": [p.as_dict() for p in store.all()],
        "kinds": list(KINDS),
        "roles": list(ROLES),
        "faces": faces,
        "accounts": accounts,
        "contacts": contacts,
    })


async def save(request: web.Request) -> web.Response:
    data = await read_json(request)
    confirm_password(request, str(data.get("password", "")))
    person_id = int(request.match_info["id"]) if "id" in request.match_info else None
    try:
        saved = _store(request).save(person_id, dict(data.get("person") or data))
    except PersonError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None
    dashboard(request).app.db.audit(_actor(request), "persons.save", str(saved))
    return web.json_response({"id": saved})


async def delete(request: web.Request) -> web.Response:
    person_id = int(request.match_info["id"])
    if not _store(request).delete(person_id):
        raise web.HTTPNotFound()
    dashboard(request).app.db.audit(_actor(request), "persons.delete", str(person_id))
    return web.json_response({"ok": True})


async def migrate(request: web.Request) -> web.Response:
    """Link existing faces, profiles and emergency contacts by name."""
    confirm_password(request, str((await read_json(request)).get("password", "")))
    d = dashboard(request)
    store = _store(request)
    faces = [name for name, *_ in d.app.faces.store.people()] if d.app.faces is not None else []
    profiles = [{"username": u, **data} for u, data in d.profiles.all().items()]
    contacts = [c.as_dict() for c in d.app.escalation.contacts.all()] if d.app.escalation else []
    created = store.migrate_existing(face_names=faces, profiles=profiles, contacts=contacts)
    d.app.db.audit(_actor(request), "persons.migrate", str(created))
    return web.json_response({"ok": True, "created": created, "persons": len(store.all())})
