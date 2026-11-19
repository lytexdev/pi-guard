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
        web.get("/api/persons/{id:\\d+}/picture", picture_get),
        web.put("/api/persons/{id:\\d+}/picture", picture_put),
        web.delete("/api/persons/{id:\\d+}/picture", picture_delete),
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
            # For the ring around a picture: admin, member or viewer.
            "account_roles": {name: d.auth.role_of(name) for name in accounts},
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


async def picture_get(request: web.Request) -> web.Response:
    data = _store(request).picture(int(request.match_info["id"]))
    if data is None:
        raise web.HTTPNotFound()
    return web.Response(body=data, content_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400"})  # fmt: skip


async def picture_put(request: web.Request) -> web.Response:
    """The JPEG itself as the body, at most PICTURE_MAX bytes."""
    from pi_guard.storage.persons import PICTURE_MAX

    if request.content_length is not None and request.content_length > PICTURE_MAX:
        raise web.HTTPRequestEntityTooLarge(
            max_size=PICTURE_MAX, actual_size=request.content_length
        )
    data = await request.content.read(PICTURE_MAX + 1)
    person_id = int(request.match_info["id"])
    try:
        found = _store(request).set_picture(person_id, data)
    except PersonError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None
    if not found:
        raise web.HTTPNotFound()
    dashboard(request).app.db.audit(_actor(request), "persons.picture", str(person_id))
    return web.json_response({"ok": True})


async def picture_delete(request: web.Request) -> web.Response:
    person_id = int(request.match_info["id"])
    if not _store(request).set_picture(person_id, None):
        raise web.HTTPNotFound()
    dashboard(request).app.db.audit(_actor(request), "persons.picture", f"{person_id} removed")
    return web.json_response({"ok": True})


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
    # Invitations carry logins: through a linked household they would reach its admins instead.
    return [c for c in dict.fromkeys(found) if c[0] != "household"]


# What an invitation can hold, and the area a member needs for each.
INVITE_ITEMS = {
    "dashboard": "/api/persons", "android": "/api/apps/invite", "desktop": "/api/apps/invite",
    "login": None, "vpn": "/api/network/wireguard/peers",
}  # fmt: skip


def _account_of(request: web.Request, person: Any) -> str:
    """The person's own further login; never the owner's, whose way back is the recovery code."""
    d = dashboard(request)
    for kind, ref in person.links:
        if kind == "web_account" and not d.auth.is_owner(ref) and d.auth.exists(ref):
            return str(ref)
    return ""


async def invite(request: web.Request) -> web.Response:
    """Chosen parts of a start, sent to the person: the dashboard address, the apps, their login
    with a new temporary password, a VPN access of their own.

    Only to one of the person's own channels, never to a typed address. A login or VPN access
    outlives the session: both need the password, and the chat service keeps what it sends.
    """
    import tempfile
    from pathlib import Path

    from pi_guard.messaging.base import ProviderUnavailableError, Reply
    from pi_guard.network.wgpeers import PeerError
    from pi_guard.web.api_apps import make_invite, versions
    from pi_guard.web.api_network import _config, _server
    from pi_guard.web.api_network import _store as peer_store
    from pi_guard.web.auth import new_temporary_password
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
    raw = data.get("items")
    items = [i for i in INVITE_ITEMS if isinstance(raw, list) and i in raw]
    if not items:
        raise web.HTTPBadRequest(text="web.inv_nothing")
    admin = d.auth.role_of(current(request).username) == "admin"
    for item in items:
        need = INVITE_ITEMS[item]
        if not admin and (need is None or not _may(request, need)):
            raise web.HTTPForbidden(text="web.member_forbidden")
    account = _account_of(request, person) if "login" in items else ""
    if "login" in items and not account:
        raise web.HTTPConflict(text="web.inv_no_account")
    peers = None
    if "login" in items or "vpn" in items:
        # Before anything is made: a wrong password must leave no link or access behind.
        confirm_password(request, str(data.get("password", "")))
    if "vpn" in items:
        peers = peer_store(request)
        _server(request)
    lang = d.settings.language
    base = (d.settings.dashboard_public_url or f"{request.scheme}://{request.host}").rstrip("/")
    lines = [_t("persons.invite_hello", lang, name=person.display_name)]
    actor = _actor(request)
    if "dashboard" in items:
        lines.append(_t("persons.invite_dashboard", lang, url=f"{base}/"))
    if "android" in items or "desktop" in items:
        path, _ = make_invite(d, person.display_name[:40], actor)
        have = versions(d.settings.data_dir)
        if "android" in items:
            lines.append(_t("persons.invite_android", lang, url=f"{base}{path}/android")
                         if have["android"] else _t("persons.invite_no_app", lang))  # fmt: skip
        if "desktop" in items:
            lines.append(_t("persons.invite_desktop", lang, url=f"{base}{path}")
                         if have["desktop"] else _t("persons.invite_no_app", lang))  # fmt: skip
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
    # Stored only once it was sent: a failed message must not lock the person out.
    temporary = new_temporary_password() if account else ""
    if account:
        lines.append(_t("persons.invite_login", lang, user=account, password=temporary))
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
    if account:
        await asyncio.to_thread(d.auth.temporary_password, account, temporary)
        d.app.db.audit(actor, "account.temporary_password", account)
    d.app.db.audit(actor, "persons.invite", f"{person.id} {channel[0]} {','.join(items)}")
    return web.json_response({"ok": True, "vpn": conf is not None, "login": bool(account)})


def _t(key: str, lang: str, **values: object) -> str:
    from pi_guard.i18n import t

    return t(key, lang, **values)
