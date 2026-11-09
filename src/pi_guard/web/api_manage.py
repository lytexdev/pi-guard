"""Settings, devices, trusted people, sounds, users and dashboard access."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from datetime import date
from pathlib import Path
from typing import Any, get_args

import numpy as np
import yaml
from aiohttp import web
from pydantic import ValidationError

from pi_guard.audio import devices as audio_devices
from pi_guard.audio.service import AudioError
from pi_guard.audio.sounds import valid_name
from pi_guard.cameras import discovery
from pi_guard.cameras.pipeline import SnapshotUnavailableError
from pi_guard.config import OVERRIDE_ENV, OVERRIDE_YAML, read_overrides
from pi_guard.config.files import EventName, FileConfig
from pi_guard.config.settings import Settings
from pi_guard.core.permissions import Role
from pi_guard.core.timeutil import utcnow
from pi_guard.detection.enrollment import EnrollmentSession, video_frames
from pi_guard.detection.faces import EnrollmentError, FaceRecognizer
from pi_guard.diagnostics.contribution import record_correction
from pi_guard.i18n import LANGUAGES, t
from pi_guard.notifications.rules import DEFAULTS, resolve
from pi_guard.recording.evidence import MAX_MINUTES, evidence_files, record_evidence
from pi_guard.recording.segments import ClipError
from pi_guard.security.files import atomic_write, is_within
from pi_guard.web.server import confirm_password, current, dashboard, read_json
from pi_guard.web.settings_schema import BY_KEY, EXPERT, FIELDS, SECRETS, to_env

PROFILE_FIELDS = {"birthday": 10, "address": 200, "email": 120, "phone": 40}
PICTURE_SIZE = 256
VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".mkv", ".m4v")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
MAX_UPLOAD = 50 * 1024 * 1024


log = logging.getLogger(__name__)
EVENT_NAMES = set(get_args(EventName))


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/settings", get_settings),
        web.put("/api/settings", put_settings),
        web.get("/api/settings/history", settings_history),
        web.post("/api/settings/ask", settings_ask),
        web.post("/api/settings/history/{id:\\d+}/revert", settings_revert),
        web.get("/api/config", get_config),
        web.put("/api/config", put_config),
        web.get("/api/notifications", notification_rules),
        web.put("/api/notifications/{event}", save_notification_rule),
        web.get("/api/devices", devices),
        web.post("/api/system/restart", restart),
        web.get("/api/people", people),
        web.post("/api/people/{name}/samples", add_samples),
        web.post("/api/people/{name}/camera", add_from_camera),
        web.post("/api/people-test", test_recognition),
        web.get("/api/events/{id:\\d+}/faces", event_faces),
        web.post("/api/events/{id:\\d+}/faces/{index:\\d+}", learn_from_event),
        web.get("/api/evidence", evidence_list),
        web.post("/api/evidence", evidence_start),
        web.get("/api/people/{name}", person_details),
        web.put("/api/people/{name}/profile", person_profile),
        web.get("/api/people/{name}/picture", person_picture),
        web.post("/api/people/{name}/picture", person_picture_save),
        web.delete("/api/people/{name}/picture", person_picture_delete),
        web.delete("/api/people-samples/{id:\\d+}", delete_sample),
        web.patch("/api/people/{name}", rename_person),
        web.delete("/api/people/{name}", delete_person),
        web.get("/api/sounds", sounds),
        web.post("/api/sounds", upload_sound),
        web.delete("/api/sounds/{name}", delete_sound),
        web.patch("/api/sounds/{name}", rename_sound),
        web.post("/api/sounds-restore", restore_sounds),
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
        web.post("/api/push/action", push_action),
    ]


def _actor(request: web.Request) -> str:
    return f"web:{current(request).username}"


# --- settings and configuration ------------------------------------------------------


async def get_settings(request: web.Request) -> web.Response:
    s = dashboard(request).settings
    return web.json_response({
        "values": {f.key: "" if f.kind == "secret" else _plain(getattr(s, f.key)) for f in FIELDS},
        "fields": [{"key": f.key, "group": f.group, "expert": f.key in EXPERT,
                    "kind": "choice" if f.kind == "language" else f.kind, "min": f.minimum,
                    "max": f.maximum,
                    "options": list(LANGUAGES) if f.kind == "language" else list(f.options)}
                   for f in FIELDS],
        "secrets": {key: getattr(s, key) is not None for key in SECRETS},
        "overrides": sorted(read_overrides(s.data_dir)),
    })  # fmt: skip


def _plain(value: Any) -> Any:
    if isinstance(value, list):
        return [str(v) for v in value]
    return value.value if hasattr(value, "value") else value


SOURCES = ("dashboard", "assistant")


async def put_settings(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    source = request.query.get("source", "dashboard")
    await apply_settings(d, data, _actor(request), source if source in SOURCES else "dashboard")
    return web.json_response({"ok": True, "restart_required": True})


async def apply_settings(
    d: Any, data: dict[str, Any], actor: str, source: str = "dashboard"
) -> None:
    """Check and write changes; they take effect at the next start."""
    rendered = dict(read_overrides(d.settings.data_dir))
    try:
        for key, value in data.items():
            field_ = BY_KEY.get(key)
            if field_ is None:
                raise ValueError(f"{key} cannot be changed here")
            if field_.kind == "secret" and not str(value).strip():
                continue  # empty means: keep the current secret
            rendered[key] = to_env(field_, value)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)[:500]) from None
    await _write_overrides(d, rendered, actor, source)


HISTORY_SCHEMA = """CREATE TABLE IF NOT EXISTS settings_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL,
    source TEXT NOT NULL, changes TEXT NOT NULL)"""
HISTORY_KEEP = 200


async def _write_overrides(d: Any, rendered: dict[str, str], actor: str, source: str) -> None:
    """Validate the whole result, write it, and keep what it replaced so it can be undone."""
    before = read_overrides(d.settings.data_dir)
    try:
        Settings(**{**_base_values(d.settings), **rendered})
    except ValidationError as exc:
        # Only messages: the input (possibly a token) is never echoed back.
        text = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'settings'}: {e['msg']}"
                         for e in exc.errors())  # fmt: skip
        raise web.HTTPBadRequest(text=text[:500]) from None
    lines = ["# Written by the pi-guard dashboard; takes precedence over pi-guard.env."]
    lines += [f"PI_GUARD_{key.upper()}={value}" for key, value in sorted(rendered.items())]
    path = d.settings.data_dir / OVERRIDE_ENV
    await asyncio.to_thread(atomic_write, path, ("\n".join(lines) + "\n").encode())
    changed = sorted(k for k in set(before) | set(rendered) if before.get(k) != rendered.get(k))
    d.app.db.audit(actor, "settings", ",".join(changed))
    if changed:
        # Secrets are never kept, not even here: such a change cannot be undone.
        changes = {k: None if k in SECRETS else [before.get(k), rendered.get(k)] for k in changed}
        db = d.app.db
        db.execute(HISTORY_SCHEMA)
        db.execute("INSERT INTO settings_history (ts, actor, source, changes) VALUES (?, ?, ?, ?)",
                   (utcnow().isoformat(), actor, source, json.dumps(changes)))  # fmt: skip
        db.execute(
            "DELETE FROM settings_history WHERE id <= (SELECT MAX(id) FROM settings_history) - ?",
            (HISTORY_KEEP,),
        )


async def settings_ask(request: web.Request) -> web.Response:
    """Explains settings, with the assistant if it runs, and may propose one change."""
    from pi_guard.addons import settings_help
    from pi_guard.addons.assistant import AssistantError

    d = dashboard(request)
    question = str((await read_json(request)).get("question", ""))
    addons = getattr(d.app, "addons", None)
    assistant = getattr(addons, "assistant", None)
    try:
        found = await settings_help.ask(assistant, d.settings, question, d.settings.language)
    except AssistantError as exc:
        raise web.HTTPConflict(text=exc.key) from None
    if found.proposal:
        d.app.db.audit(_actor(request), "settings.proposed", found.proposal["key"])
    return web.json_response({
        "answer": found.answer, "found": found.found, "proposal": found.proposal,
        "ai": found.answer is not None,
    })  # fmt: skip


async def settings_history(request: web.Request) -> web.Response:
    """The last changes, with the value before and after, so one can be undone."""
    db = dashboard(request).app.db
    db.execute(HISTORY_SCHEMA)
    rows = db.execute("SELECT id, ts, actor, source, changes FROM settings_history "
                      "ORDER BY id DESC LIMIT 50").fetchall()  # fmt: skip
    return web.json_response([{"id": r[0], "time": r[1], "actor": r[2], "source": r[3],
                               "changes": json.loads(r[4])} for r in rows])  # fmt: skip


async def settings_revert(request: web.Request) -> web.Response:
    """Puts back what one change replaced. A value missing before is removed again, so the
    setting from pi-guard.env counts. Secrets stay as they are."""
    d = dashboard(request)
    db = d.app.db
    db.execute(HISTORY_SCHEMA)
    row = db.execute("SELECT changes FROM settings_history WHERE id = ?",
                     (int(request.match_info["id"]),)).fetchone()  # fmt: skip
    if row is None:
        raise web.HTTPNotFound()
    rendered = dict(read_overrides(d.settings.data_dir))
    reverted = 0
    for key, change in json.loads(row[0]).items():
        if change is None or key not in BY_KEY:
            continue
        old = change[0]
        if old is None:
            rendered.pop(key, None)
        else:
            rendered[key] = old
        reverted += 1
    if not reverted:
        raise web.HTTPConflict(text="web.set_cannot_undo")
    await _write_overrides(d, rendered, _actor(request), "revert")
    return web.json_response({"ok": True, "restart_required": True})


def _base_values(settings: Settings) -> dict[str, Any]:
    # telegram_bot_id is derived from the token and must follow a new token.
    return {k: v for k, v in settings.model_dump().items()
            if v is not None and k != "telegram_bot_id"}  # fmt: skip


def saved_config(d: Any) -> dict[str, Any]:
    """What is written, which can be newer than what runs until the next restart."""
    path = d.settings.data_dir / OVERRIDE_YAML
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    return d.app.config.file.model_dump(mode="json")  # type: ignore[no-any-return]


async def write_config(d: Any, config: FileConfig) -> None:
    text = "# Written by the pi-guard dashboard; replaces config.yaml while it exists.\n"
    text += yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    await asyncio.to_thread(atomic_write, d.settings.data_dir / OVERRIDE_YAML, text.encode())


async def notification_rules(request: web.Request) -> web.Response:
    """Every kind of message with its current rule, for the on/off page."""
    d = dashboard(request)
    file = FileConfig.model_validate(saved_config(d))
    rows = []
    for event in sorted(DEFAULTS, key=lambda e: e.value):
        if event.value not in EVENT_NAMES:
            continue
        rule = resolve(event, {str(k): v for k, v in file.notifications.rules.items()})
        rows.append({
            "event": event.value, "enabled": rule.enabled, "priority": rule.priority,
            "modes": [m.value for m in rule.modes], "recipients": rule.recipients,
            "cooldown": rule.cooldown_seconds, "attach": rule.attach,
            "providers": list(rule.providers),
        })  # fmt: skip
    ways = [*d.app.providers, *(["push"] if d.app.webpush is not None else [])]
    return web.json_response({"rules": rows, "ways": ways})


async def save_notification_rule(request: web.Request) -> web.Response:
    d = dashboard(request)
    event = request.match_info["event"]
    if event not in EVENT_NAMES:
        raise web.HTTPNotFound()
    data = await read_json(request)
    config = saved_config(d)
    rules = config.setdefault("notifications", {}).setdefault("rules", {})
    rules[event] = {**(rules.get(event) or {}), **data}
    try:
        validated = FileConfig.model_validate(config)
    except ValidationError as exc:
        raise web.HTTPBadRequest(text=exc.errors()[0]["msg"]) from None
    await write_config(d, validated)
    d.app.db.audit(_actor(request), "notification.rule", event)
    # Takes effect at once as well, without a restart.
    object.__setattr__(d.app.config, "file", d.app.config.file.model_copy(
        update={"notifications": validated.notifications}))  # fmt: skip
    return web.json_response({"ok": True})


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
    await write_config(d, config)
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
    similar = await asyncio.to_thread(faces.similar_people)
    warn_at = faces.settings.match_threshold - faces.settings.uncertain_margin
    rows = []
    for name, count, enrolled in faces.store.people():
        other, score = similar.get(name, (None, 0.0))
        rows.append({
            "name": name, "samples": count, "enrolled": enrolled,
            "similar_to": other if other and score >= warn_at else None,
            "quality": faces.quality(name, similar),
            "profile": faces.store.profile(name),
        })  # fmt: skip
    return web.json_response(rows)


def clean_profile(data: dict[str, Any]) -> dict[str, str]:
    """Everything is optional. Empty fields are not kept."""
    values = {
        key: str(data.get(key) or "").strip()[:limit] for key, limit in PROFILE_FIELDS.items()
    }
    birthday, email = values["birthday"], values["email"]
    if birthday:
        try:
            if date.fromisoformat(birthday) > date.today():
                raise ValueError(birthday)
        except ValueError:
            raise web.HTTPBadRequest(text="web.pp_bad_birthday") from None
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise web.HTTPBadRequest(text="web.pp_bad_email")
    return {key: value for key, value in values.items() if value}


async def person_profile(request: web.Request) -> web.Response:
    faces = _faces(request)
    if not faces.store.set_profile(_name(request), clean_profile(await read_json(request))):
        raise web.HTTPNotFound()
    dashboard(request).app.db.audit(_actor(request), "faces.profile")
    return web.json_response({"ok": True})


async def person_picture(request: web.Request) -> web.Response:
    picture = _faces(request).store.picture(_name(request))
    if picture is None:
        raise web.HTTPNotFound()
    return web.Response(body=picture, content_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})  # fmt: skip


def small_picture(raw: bytes) -> bytes:
    """A square thumbnail. Written anew, so the place and time in a photo's data do not stay."""
    import cv2

    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise web.HTTPBadRequest(text="web.pp_bad_picture")
    height, width = image.shape[:2]
    side = min(height, width)
    top, left = (height - side) // 2, (width - side) // 2
    square = image[top : top + side, left : left + side]
    small = cv2.resize(square, (PICTURE_SIZE, PICTURE_SIZE), interpolation=cv2.INTER_AREA)
    return bytes(cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 85])[1])


async def person_picture_save(request: web.Request) -> web.Response:
    faces = _faces(request)
    name = _name(request)
    reader = await request.multipart()
    part = await reader.next()
    if part is None:
        raise web.HTTPBadRequest(text="web.pp_bad_picture")
    raw = await part.read()  # type: ignore[union-attr]
    picture = await asyncio.to_thread(small_picture, bytes(raw))
    if not faces.store.set_picture(name, picture):
        raise web.HTTPNotFound()
    return web.json_response({"ok": True})


async def person_picture_delete(request: web.Request) -> web.Response:
    if not _faces(request).store.set_picture(_name(request), None):
        raise web.HTTPNotFound()
    return web.json_response({"ok": True})


async def _receive(request: web.Request, workdir: Path) -> tuple[dict[str, str], list[Path]]:
    """Multipart upload: text fields and the photo/video files (saved in `workdir`)."""
    fields: dict[str, str] = {}
    files: list[Path] = []
    reader = await request.multipart()
    total = 0
    async for part in reader:
        if not getattr(part, "filename", None):
            fields[str(part.name)] = (await part.text())[:200]  # type: ignore[union-attr]
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
    return fields, files


async def add_samples(request: web.Request) -> web.Response:
    """Multipart upload of photos and videos; `consent=yes` is required."""
    faces = _faces(request)
    name = _name(request)
    workdir = Path(tempfile.mkdtemp(prefix="pi-guard-upload-"))
    try:
        fields, files = await _receive(request, workdir)
        if fields.get("consent") != "yes":
            raise web.HTTPBadRequest(text="consent required")
        session = EnrollmentSession(name=name)
        result = await _collect(faces, session, files)
        try:
            report = await asyncio.to_thread(
                faces.enroll_many, name, session.samples, _actor(request)
            )
        except EnrollmentError as exc:
            raise web.HTTPBadRequest(text=exc.key) from None
        dashboard(request).app.db.audit(_actor(request), "faces.enroll",
                                        f"samples={report.stored}")  # fmt: skip
        return web.json_response({
            **result, "stored": report.stored, "total": report.total or _sample_count(faces, name),
            "outliers": report.outliers, "conflicts": report.conflicts,
            "quality": await asyncio.to_thread(faces.quality, name),
        })  # fmt: skip
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _sample_count(faces: FaceRecognizer, name: str) -> int:
    return next((c for n, c, _ in faces.store.people() if n.casefold() == name.casefold()), 0)


async def _collect(
    faces: FaceRecognizer, session: EnrollmentSession, files: list[Path]
) -> dict[str, Any]:
    """Faces from all pictures and video frames; counts why pictures were left out."""
    limit = faces.settings.max_samples_per_person
    reasons: dict[str, int] = {}
    pictures = 0
    for path in files:
        images = [path]
        frames_dir = None
        if path.suffix in VIDEO_SUFFIXES:
            frames_dir, images = await video_frames(path)
        for image in images:
            pictures += 1
            try:
                embedding = await asyncio.to_thread(faces.prepare, image)
            except EnrollmentError as exc:
                reasons[exc.key] = reasons.get(exc.key, 0) + 1
                continue
            except ValueError:
                reasons["faces.unreadable"] = reasons.get("faces.unreadable", 0) + 1
                continue
            if not session.add(embedding, limit):
                reasons["faces.duplicate"] = reasons.get("faces.duplicate", 0) + 1
        if frames_dir is not None:
            shutil.rmtree(frames_dir, ignore_errors=True)
    return {"pictures": pictures, "skipped": sum(reasons.values()), "reasons": reasons}


async def test_recognition(request: web.Request) -> web.Response:
    """Who would pi-guard see in this photo? Nothing is stored."""
    faces = _faces(request)
    detector = dashboard(request).app.analysis
    workdir = Path(tempfile.mkdtemp(prefix="pi-guard-test-"))
    try:
        _, files = await _receive(request, workdir)
        images = [f for f in files if f.suffix in IMAGE_SUFFIXES][:1]
        if not images:
            raise web.HTTPBadRequest(text="web.test_needs_photo")
        persons = getattr(detector, "persons", None)
        if persons is None:
            raise web.HTTPConflict(text="web.faces_disabled")
        result = await asyncio.to_thread(persons.detect, images[0])
        found = []
        for face in result.faces if result.faces is not None else []:
            identity = await asyncio.to_thread(faces.identify, result.image, face)
            found.append({
                "verdict": identity.verdict.value, "name": identity.name,
                "score": round(identity.score, 2), "runner_up": identity.runner_up,
                "runner_up_score": round(identity.runner_up_score, 2), "reason": identity.reason,
            })  # fmt: skip
        return web.json_response({"faces": found, "bodies": result.bodies})
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


PERSON_EVENTS = {"person_detected", "unknown_person", "known_person"}


async def _event_picture(request: web.Request) -> tuple[FaceRecognizer, Any]:
    """The faces found in a person event's picture, found again: the event keeps no boxes."""
    faces = _faces(request)
    app = dashboard(request).app
    persons = getattr(app.analysis, "persons", None)
    if persons is None:
        raise web.HTTPConflict(text="web.faces_disabled")
    row = app.db.execute("SELECT type, data FROM events WHERE id = ?",
                         (int(request.match_info["id"]),)).fetchone()  # fmt: skip
    if row is None or row["type"] not in PERSON_EVENTS:
        raise web.HTTPNotFound()
    name = str(json.loads(row["data"]).get("snapshot") or "")
    root = app.media_dir / "snapshots"
    path = root / name
    if not name or "/" in name or not is_within(path, root) or not path.is_file():
        raise web.HTTPGone(text="web.lf_picture_gone")
    return faces, await asyncio.to_thread(persons.detect, path)


async def event_faces(request: web.Request) -> web.Response:
    """Each face in an event picture: where it is, whom pi-guard saw, whether it can be learned."""
    faces, result = await _event_picture(request)
    found = []
    for face in result.faces if result.faces is not None else []:
        identity = await asyncio.to_thread(faces.identify, result.image, face)
        problem, _ = await asyncio.to_thread(faces.learn_problem, result.image, face)
        found.append({
            "box": [round(float(v)) for v in face[:4]], "verdict": identity.verdict.value,
            "name": identity.name, "score": round(identity.score, 2), "learn_problem": problem,
        })  # fmt: skip
    height, width = result.image.shape[:2] if result.image is not None else (0, 0)
    return web.json_response({"faces": found, "width": width, "height": height,
                              "people": [n for n, _, _ in faces.store.people()]})  # fmt: skip


async def learn_from_event(request: web.Request) -> web.Response:
    """`{"name": X, "is": true}`: this face is X. `"is": false`: it is not X."""
    data = await read_json(request)
    name, is_person = str(data.get("name") or "").strip(), data.get("is")
    if not name or len(name) > 64 or not isinstance(is_person, bool):
        raise web.HTTPBadRequest(text="invalid request")
    faces, result = await _event_picture(request)
    index = int(request.match_info["index"])
    if result.faces is None or index >= len(result.faces):
        raise web.HTTPNotFound()
    face, actor = result.faces[index], _actor(request)
    try:
        if is_person:
            total, score = await asyncio.to_thread(faces.learn, name, result.image, face, actor)
        else:
            score = await asyncio.to_thread(faces.reject, name, result.image, face)
    except EnrollmentError as exc:
        raise web.HTTPUnprocessableEntity(text=exc.key) from None
    app = dashboard(request).app
    action = "faces.learn" if is_person else "faces.reject"
    app.db.audit(actor, action, f"event={request.match_info['id']}")
    record_correction(app, is_person, score)  # only the score's bin, for the statistics
    if not is_person:
        return web.json_response({"ok": True})
    quality = await asyncio.to_thread(faces.quality, faces.store.exists(name) or name)
    return web.json_response({"ok": True, "total": total, "quality": quality})


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
        count = faces.enroll(name, embedding, _actor(request))
    except EnrollmentError as exc:
        raise web.HTTPUnprocessableEntity(text=exc.key) from None
    finally:
        photo.unlink(missing_ok=True)
    app.db.audit(_actor(request), "faces.enroll", f"samples={count}")
    quality = await asyncio.to_thread(faces.quality, name)
    return web.json_response({"stored": 1, "total": count, "quality": quality})


async def evidence_list(request: web.Request) -> web.Response:
    d = dashboard(request)
    rows = await asyncio.to_thread(evidence_files, d.app.media_dir)
    return web.json_response({"files": rows, "running": sorted(d.evidence_running)})


async def evidence_start(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    camera = str(data.get("camera") or next(iter(d.app.cameras), ""))
    minutes = int(data.get("minutes") or 5)
    if camera not in d.app.cameras or not 1 <= minutes <= MAX_MINUTES:
        raise web.HTTPBadRequest(text="web.ev_bad_request")
    if camera in d.evidence_running:
        raise web.HTTPConflict(text="web.ev_running")
    actor = _actor(request)

    async def run() -> None:
        d.evidence_running.add(camera)
        try:
            await record_evidence(d.app, camera, minutes, actor)
        except (OSError, ValueError, ClipError) as exc:
            log.warning("evidence video failed: %s", exc)
        finally:
            d.evidence_running.discard(camera)

    d.background(run())
    return web.json_response({"ok": True, "minutes": minutes})


async def person_details(request: web.Request) -> web.Response:
    faces = _faces(request)
    return web.json_response(await asyncio.to_thread(faces.store.details, _name(request)))


async def delete_sample(request: web.Request) -> web.Response:
    faces = _faces(request)
    if not faces.store.delete_sample(int(request.match_info["id"])):
        raise web.HTTPNotFound()
    faces.forget_cache()
    dashboard(request).app.db.audit(_actor(request), "faces.delete_sample")
    return web.json_response({"ok": True})


async def rename_person(request: web.Request) -> web.Response:
    faces = _faces(request)
    data = await read_json(request)
    new = str(data.get("name", "")).strip()
    if not new or len(new) > 64:
        raise web.HTTPBadRequest(text="invalid name")
    if not faces.store.rename(_name(request), new):
        raise web.HTTPConflict(text="not found or name taken")
    faces.forget_cache()  # recognition would go on reporting the old name
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


async def rename_sound(request: web.Request) -> web.Response:
    new = str((await read_json(request)).get("name", "")).strip().casefold()
    if not _audio(request).sounds.rename(request.match_info["name"], new):
        raise web.HTTPBadRequest(text="web.sound_bad_name")
    return web.json_response({"ok": True, "name": new})


async def restore_sounds(request: web.Request) -> web.Response:
    _audio(request).sounds.restore_builtin()
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
        "methods": d.login_methods(),
        "totp": d.auth.account()["totp"],
    })  # fmt: skip


async def create_keyfile(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    name = str(data.get("name", "")).strip()
    if not name or len(name) > 40 or not name.replace("-", "").replace("_", "").isalnum():
        raise web.HTTPBadRequest(text="name: letters, digits, - and _")
    confirm_password(request, str(data.get("password", "")))
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


async def push_action(request: web.Request) -> web.Response:
    """A button in a push notification. No session: the token in it is the permission."""
    from pi_guard.notifications import actions

    d = dashboard(request)
    sender = d.app.webpush
    data = await read_json(request)
    action, token = str(data.get("action", "")), str(data.get("token", ""))
    ip = d.guard.client_ip(request)
    if not d.guard.login_allowed(ip):
        raise web.HTTPTooManyRequests(text="Too many attempts")
    username = sender.use_action_token(token) if sender is not None and token else None
    if username is None or d.auth.role_of(username) != "admin" or action not in actions.ACTIONS:
        d.guard.login_failed(ip)
        raise web.HTTPForbidden(text="web.push_action_expired")
    try:
        result = actions.run(d.app, f"web:{username}", action)
    except actions.ActionError as exc:
        raise web.HTTPConflict(text=exc.key) from None
    return web.json_response(result)


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
