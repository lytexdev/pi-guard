"""Central person records for the dashboard (faces, accounts, messengers, emergency)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from pi_guard.storage.persons import KINDS, ROLES, PersonError, Persons
from pi_guard.web import permissions
from pi_guard.web.server import current, dashboard, read_json

log = logging.getLogger(__name__)


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/persons", listing),
        web.post("/api/persons", save),
        web.put("/api/persons/{id:\\d+}", save),
        web.delete("/api/persons/{id:\\d+}", delete),
        web.post("/api/persons/migrate", migrate),
        web.post("/api/persons/{id:\\d+}/invite", invite),
    ]


def _store(request: web.Request) -> Persons:
    return Persons(dashboard(request).app.db)


def _actor(request: web.Request) -> str:
    return f"web:{current(request).username}"


def _may(request: web.Request, path: str) -> bool:
    """Whether this session may also use another area: the contacts grant alone must not
    hand out app invitations or VPN accesses."""
    d = dashboard(request)
    name = current(request).username
    role = d.auth.role_of(name)
    if role == "admin":
        return True
    return role == "member" and permissions.allowed(d.auth.permissions_of(name), "POST", path)


def _face_names(request: web.Request) -> list[dict[str, object]]:
    """Enrolled face names even when recognition is currently off."""
    d = dashboard(request)
    if d.app.faces is not None:
        return [
            {"name": name, "samples": int(count)} for name, count, _ in d.app.faces.store.people()
        ]
    path = d.settings.data_dir / "biometrics.db"
    if not path.is_file():
        return []
    from pi_guard.detection.faces import BiometricStore

    store = BiometricStore(path)
    try:
        return [{"name": name, "samples": int(count)} for name, count, _ in store.people()]
    finally:
        store.close()


async def listing(request: web.Request) -> web.Response:
    d = dashboard(request)
    store = _store(request)
    faces = _face_names(request)
    accounts = [d.auth.account()["username"], *(u["username"] for u in d.auth.users())]
    contacts = [c.as_dict() for c in d.app.escalation.contacts.all()] if d.app.escalation else []
    medical = []
    if d.app.addons.medical is not None and _may(request, "/api/medical"):
        medical = d.app.addons.medical.people()  # names only, still not for every member
    return web.json_response(
        {
            "persons": [p.as_dict() for p in store.all()],
            "kinds": list(KINDS),
            "roles": list(ROLES),
            "faces": faces,
            "faces_on": d.app.faces is not None,
            "accounts": accounts,
            "contacts": contacts,
            "medical": medical,
            "medical_available": d.app.addons.medical is not None,
        }
    )


async def save(request: web.Request) -> web.Response:
    # Admin session + CSRF is enough: no durable login secret is created here.
    data = await read_json(request)
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
    await read_json(request)
    d = dashboard(request)
    store = _store(request)
    faces = [name for name, *_ in d.app.faces.store.people()] if d.app.faces is not None else []
    profiles = [{"username": u, **data} for u, data in d.profiles.all().items()]
    contacts = [c.as_dict() for c in d.app.escalation.contacts.all()] if d.app.escalation else []
    created = store.migrate_existing(face_names=faces, profiles=profiles, contacts=contacts)
    d.app.db.audit(_actor(request), "persons.migrate", str(created))
    return web.json_response({"ok": True, "created": created, "persons": len(store.all())})


def _channels(request: web.Request, person: Any) -> list[tuple[str, str]]:
    """Where this person can be written to: their messengers and the email of their contact."""
    d = dashboard(request)
    found: list[tuple[str, str]] = []
    for kind, ref in person.links:
        if kind == "messenger" and ":" in ref:
            provider, address = ref.split(":", 1)
            found.append((provider, address))
        if kind == "emergency_contact" and d.app.escalation is not None:
            contact = d.app.escalation.contacts.get(int(ref)) if ref.isdigit() else None
            if contact is not None:
                found += list(contact.channels)
                if contact.email:
                    found.append(("email", contact.email))
    return list(dict.fromkeys(found))


async def invite(request: web.Request) -> web.Response:
    """The apps, and on request a VPN access of their own, sent to the person.

    Only to one of the person's own channels, never to a typed address. A VPN access is a key to
    the home network: it needs the password, and the chat service keeps the file it sends.
    """
    import tempfile
    from pathlib import Path

    from pi_guard.messaging.base import ProviderUnavailableError, Reply
    from pi_guard.network.wgpeers import PeerError
    from pi_guard.web.api_apps import make_invite
    from pi_guard.web.api_network import _config, _server
    from pi_guard.web.api_network import _store as peer_store
    from pi_guard.web.server import confirm_password

    d = dashboard(request)
    store = _store(request)
    person = store.get(int(request.match_info["id"]))
    if person is None:
        raise web.HTTPNotFound()
    data = await read_json(request)
    channel = (str(data.get("provider", "")), str(data.get("address", "")))
    if channel not in _channels(request, person):
        raise web.HTTPBadRequest(text="web.inv_bad_channel")
    if channel[0] not in d.app.providers:
        raise web.HTTPConflict(text="web.inv_channel_off")
    want_app, want_vpn = data.get("app") is not False, data.get("vpn") is True
    if not (want_app or want_vpn):
        raise web.HTTPBadRequest(text="web.inv_nothing")
    if (want_app and not _may(request, "/api/apps/invite")) or (
        want_vpn and not _may(request, "/api/network/wireguard/peers")
    ):
        raise web.HTTPForbidden(text="web.member_forbidden")
    peers = None
    if want_vpn:
        # Before anything is made: a wrong password must leave no link or access behind.
        confirm_password(request, str(data.get("password", "")))
        peers = peer_store(request)
        _server(request)
    lang = d.settings.language
    base = (d.settings.dashboard_public_url or f"{request.scheme}://{request.host}").rstrip("/")
    lines = [_t("persons.invite_hello", lang, name=person.display_name)]
    actor = _actor(request)
    if want_app:
        path, _ = make_invite(d, person.display_name[:40], actor)
        lines.append(_t("persons.invite_app", lang, url=f"{base}{path}"))
    conf: tuple[str, str] | None = None
    peer_id: int | None = None
    if peers is not None:
        name = person.display_name[:24]
        taken = {p["name"].casefold() for p in peers.list()}
        for n in range(2, 50):
            if name.casefold() not in taken:
                break
            name = f"{person.display_name[:24]} {n}"
        try:
            peer_id = await asyncio.to_thread(peers.add, name, True)
        except PeerError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from None
        try:
            conf = _config(request, peer_id)
        except web.HTTPException:
            await asyncio.to_thread(peers.remove, peer_id)
            raise
        store.link(person.id, "vpn", str(peer_id))
        d.app.db.audit(actor, "wireguard.add", conf[0])
        lines.append(_t("persons.invite_vpn", lang))
    with tempfile.TemporaryDirectory(prefix="pg-invite-") as folder:
        document = None
        if conf is not None:
            document = Path(folder) / "pi-guard-vpn.conf"
            document.write_text(conf[1])
            document.chmod(0o600)
        try:
            await d.app.messenger.send(channel[0], channel[1],
                                       Reply("\n\n".join(lines), document=document))  # fmt: skip
        except Exception as exc:  # every messenger fails in its own way
            reason = (
                "ProviderUnavailable"
                if isinstance(exc, ProviderUnavailableError)
                else type(exc).__name__
            )
            log.warning("invitation via %s failed: %s", channel[0], reason)
            if peers is not None and peer_id is not None:
                # Nobody got this access: it must not stay open.
                await asyncio.to_thread(peers.remove, peer_id)
                store.unlink("vpn", str(peer_id))
                d.app.db.audit(actor, "wireguard.remove", conf[0] if conf else str(peer_id))
            raise web.HTTPBadGateway(text="web.ec_unreachable") from None
    d.app.db.audit(actor, "persons.invite",
                   f"{person.id} {channel[0]} app={want_app} vpn={want_vpn}")  # fmt: skip
    return web.json_response({"ok": True, "vpn": conf is not None})


def _t(key: str, lang: str, **values: object) -> str:
    from pi_guard.i18n import t

    return t(key, lang, **values)
