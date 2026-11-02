"""Settings, devices, trusted people, sounds, users and dashboard access."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import yaml
from aiohttp import web
from pydantic import ValidationError

from pi_guard.audio import devices as audio_devices
from pi_guard.audio.service import AudioError
from pi_guard.audio.sounds import valid_name
from pi_guard.cameras import discovery
from pi_guard.cameras.pipeline import SnapshotUnavailableError
from pi_guard.config import OVERRIDE_ENV, OVERRIDE_YAML, read_overrides
from pi_guard.config.files import FileConfig
from pi_guard.config.settings import Settings
from pi_guard.core.permissions import Role
from pi_guard.detection.enrollment import EnrollmentSession, video_frames
from pi_guard.detection.faces import EnrollmentError, FaceRecognizer
from pi_guard.i18n import t
from pi_guard.security.files import atomic_write
from pi_guard.web.server import current, dashboard, read_json
from pi_guard.web.settings_schema import BY_KEY, FIELDS, SECRETS, to_env

VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".mkv", ".m4v")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
MAX_UPLOAD = 50 * 1024 * 1024


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/settings", get_settings),
        web.put("/api/settings", put_settings),
        web.get("/api/config", get_config),
        web.put("/api/config", put_config),
        web.get("/api/devices", devices),
        web.post("/api/system/restart", restart),
        web.get("/api/people", people),
        web.post("/api/people/{name}/samples", add_samples),
        web.post("/api/people/{name}/camera", add_from_camera),
        web.patch("/api/people/{name}", rename_person),
        web.delete("/api/people/{name}", delete_person),
        web.get("/api/sounds", sounds),
        web.post("/api/sounds", upload_sound),
        web.delete("/api/sounds/{name}", delete_sound),
        web.post("/api/sounds/{name}/play", play_sound),
        web.put("/api/sounds/alarm", set_alarm_sound),
        web.put("/api/volume", set_volume),
        web.post("/api/alarm", alarm),
        web.get("/api/users", users),
        web.delete("/api/users/{provider}/{user_id}", remove_user),
        web.post("/api/invite", invite),
        web.get("/api/access", access),
        web.post("/api/keyfiles", create_keyfile),
        web.delete("/api/keyfiles/{name}", delete_keyfile),
        web.post("/api/sessions/logout-all", logout_all),
        web.delete("/api/bans/{ip}", unban),
        web.get("/api/models", models),
        web.post("/api/models/download", download_models),
        web.get("/api/push", push_info),
        web.post("/api/push/subscribe", push_subscribe),
        web.post("/api/push/unsubscribe", push_unsubscribe),
        web.post("/api/push/test", push_test),
    ]


def _actor(request: web.Request) -> str:
    return f"web:{current(request).username}"


# --- settings and configuration ------------------------------------------------------


async def get_settings(request: web.Request) -> web.Response:
    s = dashboard(request).settings
    return web.json_response({
        "values": {f.key: "" if f.kind == "secret" else _plain(getattr(s, f.key)) for f in FIELDS},
        "fields": [{"key": f.key, "group": f.group, "kind": f.kind, "min": f.minimum,
                    "max": f.maximum, "options": list(f.options)} for f in FIELDS],
        "secrets": {key: getattr(s, key) is not None for key in SECRETS},
        "overrides": sorted(read_overrides(s.data_dir)),
    })  # fmt: skip


def _plain(value: Any) -> Any:
    if isinstance(value, list):
        return [str(v) for v in value]
    return value.value if hasattr(value, "value") else value


async def put_settings(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    current_overrides = read_overrides(d.settings.data_dir)
    rendered = dict(current_overrides)
    try:
        for key, value in data.items():
            field_ = BY_KEY.get(key)
            if field_ is None:
                raise ValueError(f"{key} cannot be changed here")
            if field_.kind == "secret" and not str(value).strip():
                continue  # empty means: keep the current secret
            rendered[key] = to_env(field_, value)
        # Validate the complete result before anything is written.
        Settings(**{**_base_values(d.settings), **rendered})
    except ValidationError as exc:
        # Only messages: the input (possibly a token) is never echoed back.
        text = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'settings'}: {e['msg']}"
                         for e in exc.errors())  # fmt: skip
        raise web.HTTPBadRequest(text=text[:500]) from None
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)[:500]) from None
    lines = ["# Written by the pi-guard dashboard; takes precedence over pi-guard.env."]
    lines += [f"PI_GUARD_{key.upper()}={value}" for key, value in sorted(rendered.items())]
    path = d.settings.data_dir / OVERRIDE_ENV
    await asyncio.to_thread(atomic_write, path, ("\n".join(lines) + "\n").encode())
    d.app.db.audit(_actor(request), "settings", ",".join(sorted(data)))
    return web.json_response({"ok": True, "restart_required": True})


def _base_values(settings: Settings) -> dict[str, Any]:
    # telegram_bot_id is derived from the token and must follow a new token.
    return {k: v for k, v in settings.model_dump().items()
            if v is not None and k != "telegram_bot_id"}  # fmt: skip


async def get_config(request: web.Request) -> web.Response:
    return web.json_response(dashboard(request).app.config.file.model_dump(mode="json"))


async def put_config(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    try:
        config = FileConfig.model_validate(data)
    except ValidationError as exc:
        errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:10]]
        raise web.HTTPBadRequest(text="\n".join(errors)) from None
    text = "# Written by the pi-guard dashboard; replaces config.yaml while it exists.\n"
    text += yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    await asyncio.to_thread(atomic_write, d.settings.data_dir / OVERRIDE_YAML, text.encode())
    d.app.db.audit(_actor(request), "config")
    return web.json_response({"ok": True, "restart_required": True})


async def devices(request: web.Request) -> web.Response:
    cameras, sound = await asyncio.gather(
        asyncio.to_thread(discovery.discover), asyncio.to_thread(audio_devices.discover)
    )
    return web.json_response({
        "cameras": [{"path": str(c.path), "node": str(c.node), "name": c.name, "slug": c.slug,
                     "formats": [{"name": f.name, "sizes": [f"{w}x{h}" for w, h in f.sizes]}
                                 for f in c.formats]} for c in cameras],
        "audio": [{"device": a.alsa_name, "name": a.name, "playback": a.playback,
                   "capture": a.capture} for a in sound],
    })  # fmt: skip


async def restart(request: web.Request) -> web.Response:
    """Exit so systemd (Restart=always) or Docker (restart policy) starts us again."""
    d = dashboard(request)
    d.app.db.audit(_actor(request), "restart")
    loop = asyncio.get_running_loop()
    loop.call_later(1.0, os._exit, 0)
    return web.json_response({"ok": True})


# --- trusted people -------------------------------------------------------------------


def _faces(request: web.Request) -> FaceRecognizer:
    faces = dashboard(request).app.faces
    if faces is None:
        raise web.HTTPConflict(text="face recognition is not enabled")
    return faces


def _name(request: web.Request) -> str:
    name = request.match_info["name"].strip()
    if not name or len(name) > 64:
        raise web.HTTPBadRequest(text="invalid name")
    return name


async def people(request: web.Request) -> web.Response:
    faces = _faces(request)
    return web.json_response([{"name": n, "samples": c, "enrolled": e}
                              for n, c, e in faces.store.people()])  # fmt: skip


async def add_samples(request: web.Request) -> web.Response:
    """Multipart upload of photos and videos; `consent=yes` is required."""
    faces = _faces(request)
    name = _name(request)
    workdir = Path(tempfile.mkdtemp(prefix="pi-guard-upload-"))
    consent, files = False, []
    try:
        reader = await request.multipart()
        total = 0
        async for part in reader:
            if part.name == "consent":  # type: ignore[union-attr]
                consent = (await part.text()) == "yes"  # type: ignore[union-attr]
                continue
            suffix = Path(part.filename or "").suffix.lower()  # type: ignore[union-attr]
            if suffix not in IMAGE_SUFFIXES + VIDEO_SUFFIXES:
                raise web.HTTPBadRequest(text=f"unsupported file type {suffix}")
            path = workdir / f"{uuid.uuid4().hex}{suffix}"
            with path.open("wb") as out:
                while chunk := await part.read_chunk():  # type: ignore[union-attr]
                    total += len(chunk)
                    if total > MAX_UPLOAD:
                        raise web.HTTPRequestEntityTooLarge(max_size=MAX_UPLOAD, actual_size=total)
                    out.write(chunk)
            files.append(path)
        if not consent:
            raise web.HTTPBadRequest(text="consent required")
        session = EnrollmentSession(name=name)
        result = await _collect(faces, session, files)
        count = 0
        for embedding in session.samples:
            count = faces.enroll(name, embedding, _actor(request))
        dashboard(request).app.db.audit(_actor(request), "faces.enroll", f"samples={count}")
        return web.json_response({**result, "stored": len(session.samples), "total": count})
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def _collect(
    faces: FaceRecognizer, session: EnrollmentSession, files: list[Path]
) -> dict[str, int]:
    limit = faces.settings.max_samples_per_person
    skipped = 0
    for path in files:
        images = [path]
        frames_dir = None
        if path.suffix in VIDEO_SUFFIXES:
            frames_dir, images = await video_frames(path)
        for image in images:
            try:
                embedding = await asyncio.to_thread(faces.prepare, image)
            except EnrollmentError:
                skipped += 1
                continue
            if not session.add(embedding, limit):
                skipped += 1
        if frames_dir is not None:
            shutil.rmtree(frames_dir, ignore_errors=True)
    return {"skipped": skipped}


async def add_from_camera(request: web.Request) -> web.Response:
    faces = _faces(request)
    name = _name(request)
    data = await read_json(request)
    if data.get("consent") is not True:
        raise web.HTTPBadRequest(text="consent required")
    app = dashboard(request).app
    camera_id = str(data.get("camera") or next(iter(app.cameras), ""))
    if camera_id not in app.cameras:
        raise web.HTTPBadRequest(text="unknown camera")
    try:
        photo = await app.take_photo(camera_id, prefix="enroll")
    except SnapshotUnavailableError:
        raise web.HTTPServiceUnavailable(text="no picture from the camera") from None
    try:
        embedding = await asyncio.to_thread(faces.prepare, photo)
    except EnrollmentError as exc:
        raise web.HTTPUnprocessableEntity(text=exc.key) from None
    finally:
        photo.unlink(missing_ok=True)
    count = faces.enroll(name, embedding, _actor(request))
    app.db.audit(_actor(request), "faces.enroll", f"samples={count}")
    return web.json_response({"stored": 1, "total": count})


async def rename_person(request: web.Request) -> web.Response:
    faces = _faces(request)
    data = await read_json(request)
    new = str(data.get("name", "")).strip()
    if not new or len(new) > 64:
        raise web.HTTPBadRequest(text="invalid name")
    if not faces.store.rename(_name(request), new):
        raise web.HTTPConflict(text="not found or name taken")
    return web.json_response({"ok": True})


async def delete_person(request: web.Request) -> web.Response:
    faces = _faces(request)
    if not faces.delete(_name(request)):
        raise web.HTTPNotFound()
    dashboard(request).app.db.audit(_actor(request), "faces.delete")
    return web.json_response({"ok": True})


# --- sounds, volume, alarm -----------------------------------------------------------------


def _audio(request: web.Request) -> Any:
    audio = dashboard(request).app.audio
    if audio is None:
        raise web.HTTPConflict(text="audio is not set up")
    return audio


async def sounds(request: web.Request) -> web.Response:
    audio = _audio(request)
    alarm = audio.alarm_sound()
    return web.json_response({
        "sounds": audio.sounds.names(), "volume": audio.volume, "max_volume": audio.max_volume,
        "alarm_sound": alarm.stem if alarm else None, "alarm_active": audio.alarm_active,
        "playback": audio.playback_enabled, "recording": audio.recording_enabled,
    })  # fmt: skip


async def upload_sound(request: web.Request) -> web.Response:
    audio = _audio(request)
    reader = await request.multipart()
    name, path = "", None
    workdir = Path(tempfile.mkdtemp(prefix="pi-guard-sound-"))
    try:
        async for part in reader:
            if part.name == "name":  # type: ignore[union-attr]
                name = (await part.text()).strip().casefold()  # type: ignore[union-attr]
            elif part.name == "file":  # type: ignore[union-attr]
                suffix = Path(part.filename or "x.ogg").suffix.lower()[:6]  # type: ignore[union-attr]
                path = workdir / f"upload{suffix}"
                data = await part.read(decode=False)  # type: ignore[union-attr]
                if len(data) > audio.settings.max_upload_bytes:
                    raise web.HTTPRequestEntityTooLarge(
                        max_size=audio.settings.max_upload_bytes, actual_size=len(data)
                    )
                path.write_bytes(data)
        if path is None or not valid_name(name):
            raise web.HTTPBadRequest(text="name (a-z, 0-9, _) and file required")
        try:
            await audio.validate_upload(path)
            audio.sounds.save(name, path)
        except AudioError as exc:
            raise web.HTTPBadRequest(text=exc.key) from None
        except ValueError:
            raise web.HTTPBadRequest(text="invalid name") from None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    dashboard(request).app.db.audit(_actor(request), "sound.upload", name)
    return web.json_response({"ok": True, "name": name})


async def delete_sound(request: web.Request) -> web.Response:
    if not _audio(request).sounds.delete(request.match_info["name"]):
        raise web.HTTPNotFound()
    return web.json_response({"ok": True})


async def play_sound(request: web.Request) -> web.Response:
    audio = _audio(request)
    path = audio.sounds.path(request.match_info["name"])
    if path is None:
        raise web.HTTPNotFound()
    try:
        await audio.play(path)
    except AudioError as exc:
        raise web.HTTPConflict(text=exc.key) from None
    dashboard(request).app.db.audit(_actor(request), "audio.play", path.stem)
    return web.json_response({"ok": True})


async def set_alarm_sound(request: web.Request) -> web.Response:
    audio = _audio(request)
    name = str((await read_json(request)).get("name", ""))
    if audio.sounds.path(name) is None:
        raise web.HTTPNotFound()
    audio.set_alarm_sound(name)
    return web.json_response({"ok": True})


async def set_volume(request: web.Request) -> web.Response:
    audio = _audio(request)
    level = int((await read_json(request)).get("volume", audio.volume))
    return web.json_response({"volume": audio.set_volume(level)})


async def alarm(request: web.Request) -> web.Response:
    audio = _audio(request)
    on = (await read_json(request)).get("on") is True
    try:
        if on:
            audio.start_alarm(30)
        else:
            await audio.stop_alarm()
    except AudioError as exc:
        raise web.HTTPConflict(text=exc.key) from None
    dashboard(request).app.db.audit(_actor(request), "alarm.start" if on else "alarm.stop")
    return web.json_response({"alarm_active": audio.alarm_active})


# --- users and dashboard access -------------------------------------------------------


async def users(request: web.Request) -> web.Response:
    app = dashboard(request).app
    return web.json_response([
        {"provider": u.provider, "user_id": u.user_id, "name": u.name, "role": u.role.value,
         "configured": u.configured, "notifications": u.notifications, "language": u.language}
        for u in app.users.all()
    ])  # fmt: skip


async def remove_user(request: web.Request) -> web.Response:
    app = dashboard(request).app
    provider, user_id = request.match_info["provider"], request.match_info["user_id"]
    if not app.users.remove(provider, user_id):
        raise web.HTTPConflict(text="configured users can only be removed in the env file")
    app.db.audit(_actor(request), "user.remove", f"{provider}:{user_id}")
    return web.json_response({"ok": True})


async def invite(request: web.Request) -> web.Response:
    from datetime import timedelta

    app = dashboard(request).app
    role = Role(str((await read_json(request)).get("role", "viewer")))
    code, expires = app.users.create_invite(role, _actor(request), timedelta(hours=24))
    telegram = app.providers.get("telegram")
    username = getattr(telegram, "username", None)
    link = f"https://t.me/{username}?start={code}" if username else None
    return web.json_response({"code": code, "expires": expires.isoformat(), "link": link})


async def access(request: web.Request) -> web.Response:
    d = dashboard(request)
    return web.json_response({
        "keyfiles": d.auth.keyfiles(),
        "sessions": d.auth.sessions(),
        "bans": d.guard.bans(),
        "methods": sorted(d.settings.dashboard_methods),
        "totp": d.auth.account()["totp"],
    })  # fmt: skip


async def create_keyfile(request: web.Request) -> web.Response:
    d = dashboard(request)
    name = str((await read_json(request)).get("name", "")).strip()
    if not name or len(name) > 40 or not name.replace("-", "").replace("_", "").isalnum():
        raise web.HTTPBadRequest(text="name: letters, digits, - and _")
    secret = d.auth.create_keyfile(name)
    d.app.db.audit(_actor(request), "keyfile.create", name)
    return web.Response(text=secret + "\n", headers={
        "Content-Type": "application/octet-stream",
        "Content-Disposition": f'attachment; filename="pi-guard-{name}.key"',
    })  # fmt: skip


async def delete_keyfile(request: web.Request) -> web.Response:
    d = dashboard(request)
    if not d.auth.delete_keyfile(request.match_info["name"]):
        raise web.HTTPNotFound()
    d.app.db.audit(_actor(request), "keyfile.delete", request.match_info["name"])
    return web.json_response({"ok": True})


async def logout_all(request: web.Request) -> web.Response:
    d = dashboard(request)
    count = d.auth.end_all()
    d.app.db.audit(_actor(request), "sessions.logout_all", str(count))
    return web.json_response({"ended": count})


async def unban(request: web.Request) -> web.Response:
    dashboard(request).guard.unban(request.match_info["ip"])
    return web.json_response({"ok": True})


# --- push notifications -------------------------------------------------------------------


def _push(request: web.Request) -> Any:
    sender = dashboard(request).app.webpush
    if sender is None:
        raise web.HTTPConflict(text="push notifications are switched off")
    return sender


async def push_info(request: web.Request) -> web.Response:
    sender = dashboard(request).app.webpush
    if sender is None:
        return web.json_response({"enabled": False})
    return web.json_response({"enabled": True, "public_key": sender.public_key,
                              "devices": len(sender.subscriptions())})  # fmt: skip


async def push_subscribe(request: web.Request) -> web.Response:
    data = await read_json(request)
    try:
        _push(request).subscribe(data.get("subscription") or {}, str(data.get("language", "")),
                                 current(request).username)  # fmt: skip
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from None
    return web.json_response({"ok": True})


async def push_unsubscribe(request: web.Request) -> web.Response:
    data = await read_json(request)
    return web.json_response({"removed": _push(request).unsubscribe(str(data.get("endpoint", "")))})


async def push_test(request: web.Request) -> web.Response:
    sender = _push(request)
    texts = {
        lang: {"title": "pi-guard", "body": t("web.push_test_body", lang), "tag": "test",
               "priority": "normal", "url": "/#/overview"}
        for lang in ("de", "en")
    }  # fmt: skip
    return web.json_response({"delivered": await sender.send(texts)})


# --- detection models -------------------------------------------------------------------


async def models(request: web.Request) -> web.Response:
    from pi_guard.detection.models import MODELS

    models_dir = dashboard(request).settings.data_dir / "models"
    return web.json_response([
        {"name": m.name, "file": m.filename, "size_mb": m.size_mb, "license": m.license,
         "source": m.url, "purpose": m.purpose, "installed": m.present(models_dir)}
        for m in MODELS
    ])  # fmt: skip


async def download_models(request: web.Request) -> web.Response:
    """Downloads only after the user confirmed size and source in the dashboard."""
    from pi_guard.detection.models import MODELS, ChecksumError, download

    d = dashboard(request)
    if (await read_json(request)).get("confirmed") is not True:
        raise web.HTTPBadRequest(text="confirmation required")
    models_dir = d.settings.data_dir / "models"
    fetched = []
    for model in MODELS:
        if model.present(models_dir):
            continue
        try:
            await asyncio.to_thread(download, model, models_dir)
        except (ChecksumError, OSError) as exc:
            raise web.HTTPBadGateway(text=f"{model.filename}: {exc}") from None
        fetched.append(model.name)
    d.app.db.audit(_actor(request), "models.download", ",".join(fetched))
    return web.json_response({"downloaded": fetched, "restart_required": bool(fetched)})
