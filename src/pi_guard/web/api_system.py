"""Updates, own add-ons and pinned navigation entries."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import shutil
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
from aiohttp import web

from pi_guard import __version__
from pi_guard.addons.plugin import (
    MANIFESTS,
    TEMPLATE,
    UPLOAD_DIR,
    ManifestError,
    check_manifest,
    read_manifest,
)
from pi_guard.addons.plugin_updates import PluginGitError
from pi_guard.config.files import FileConfig
from pi_guard.web.api_manage import saved_config, write_config
from pi_guard.web.server import current, dashboard, read_json

REPOSITORY = "lytexdev/pi-guard"
CHECK_SECONDS = 3600
MAX_ZIP = 2_000_000
MAX_FILES = 100
MAX_SOURCE = 200_000
_ID_RE = re.compile(r"^[a-z0-9_]{2,32}$")
_latest: tuple[float, dict[str, Any]] = (0.0, {})


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/system/update", update_status),
        web.post("/api/system/update", update_start),
        web.put("/api/navigation/pinned", save_pinned),
        web.get("/api/plugin-manager", plugin_list),
        web.post("/api/plugin-manager", plugin_upload),
        web.post("/api/plugin-manager/create", plugin_create),
        web.post("/api/plugin-manager/git", plugin_from_git),
        web.post("/api/plugin-manager/{id}/check", plugin_check),
        web.post("/api/plugin-manager/{id}/update", plugin_update),
        web.put("/api/plugin-manager/{id}/auto", plugin_auto),
        web.get("/api/plugin-manager/{id}/panel", plugin_panel),
        web.post("/api/plugin-manager/{id}/action/{name}", plugin_action),
        web.put("/api/plugin-manager/{id}/settings", plugin_settings),
        web.put("/api/plugin-manager/{id}/enabled", plugin_toggle),
        web.get("/api/plugin-manager/{id}/source", plugin_source),
        web.put("/api/plugin-manager/{id}/source", plugin_save_source),
        web.delete("/api/plugin-manager/{id}", plugin_delete),
    ]


# --- updates ------------------------------------------------------------------------------


def _update_dir(request: web.Request) -> Path:
    return dashboard(request).settings.data_dir / "update"


async def _remote() -> dict[str, Any]:
    global _latest
    if time.monotonic() - _latest[0] < CHECK_SECONDS and _latest[1]:
        return _latest[1]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"https://api.github.com/repos/{REPOSITORY}/commits/main",
                headers={"Accept": "application/vnd.github+json"},
            )
        response.raise_for_status()
        data = response.json()
        info = {"commit": data["sha"][:10], "date": data["commit"]["committer"]["date"],
                "message": data["commit"]["message"].split("\n")[0][:120]}  # fmt: skip
    except (httpx.HTTPError, KeyError, ValueError):
        return _latest[1]
    _latest = (time.monotonic(), info)
    return info


async def update_status(request: web.Request) -> web.Response:
    directory = _update_dir(request)
    state: dict[str, Any] = {}
    status = directory / "status.json"
    if status.exists():
        try:
            state = json.loads(status.read_text())
        except ValueError:
            state = {}
    commit_file = Path("/opt/pi-guard/commit")
    commit = commit_file.read_text().strip()[:10] if commit_file.exists() else None
    remote = await _remote()
    return web.json_response({
        "version": __version__, "commit": commit, "remote": remote, "state": state,
        "available": bool(commit and remote.get("commit") and commit != remote["commit"]),
        "installed_by_script": commit_file.exists(),
        "pending": (directory / "request").exists(),
    })  # fmt: skip


async def update_start(request: web.Request) -> web.Response:
    """Only leaves a note: a root job (pi-guard-update.path) does the work and checks health."""
    directory = _update_dir(request)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "request").write_text(str(time.time()))
    d = dashboard(request)
    d.app.db.audit(f"web:{current(request).username}", "system.update")
    return web.json_response({"ok": True})


async def save_pinned(request: web.Request) -> web.Response:
    data = await read_json(request)
    pinned = [str(x) for x in data.get("pinned", []) if _ID_RE.match(str(x))][:8]
    dashboard(request).app.db.set("web.pinned", pinned)
    return web.json_response({"pinned": pinned})


# --- own add-ons --------------------------------------------------------------------------


def _root(request: web.Request) -> Path:
    return dashboard(request).settings.data_dir / UPLOAD_DIR


def _manifest(directory: Path) -> dict[str, Any] | None:
    try:
        return read_manifest(directory)
    except (OSError, ValueError):
        return None


def _checked(data: Any) -> dict[str, Any]:
    try:
        return check_manifest(data)
    except (ManifestError, ValueError, TypeError):
        raise web.HTTPBadRequest(text="web.pm_bad_manifest") from None


def unpack(archive: bytes, root: Path) -> dict[str, Any]:
    """Check a zip completely before anything is written."""
    if len(archive) > MAX_ZIP:
        raise web.HTTPRequestEntityTooLarge(max_size=MAX_ZIP, actual_size=len(archive))
    try:
        bundle = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile:
        raise web.HTTPBadRequest(text="web.pm_bad_zip") from None
    members = bundle.infolist()
    if len(members) > MAX_FILES or sum(m.file_size for m in members) > 10 * MAX_ZIP:
        raise web.HTTPBadRequest(text="web.pm_bad_zip")
    for member in members:
        path = PurePosixPath(member.filename)
        is_link = (member.external_attr >> 16) & 0o170000 == 0o120000
        if path.is_absolute() or ".." in path.parts or "\\" in member.filename or is_link:
            raise web.HTTPBadRequest(text="web.pm_bad_zip")
    names = bundle.namelist()
    found = next((n for n in MANIFESTS if n in names), None)
    if found is None:
        raise web.HTTPBadRequest(text="web.pm_bad_manifest")
    try:
        manifest = _checked(json.loads(bundle.read(found)))
    except ValueError:
        raise web.HTTPBadRequest(text="web.pm_bad_manifest") from None
    if manifest["entry"] not in bundle.namelist():
        raise web.HTTPBadRequest(text="web.pm_bad_manifest")
    target = root / manifest["id"]
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    bundle.extractall(target)
    manifest["sha256"] = hashlib.sha256(archive).hexdigest()
    return manifest


def _confirmed(request: web.Request, password: str) -> None:
    d = dashboard(request)
    if d.auth.check_password(d.auth.account()["username"], password) is None:
        raise web.HTTPForbidden(text="web.wrong_password")


async def _set_enabled(request: web.Request, plugin_id: str, enabled: bool) -> None:
    d = dashboard(request)
    config = saved_config(d)
    plugins = config.setdefault("addons", {}).setdefault("plugins", {})
    plugins[plugin_id] = {**(plugins.get(plugin_id) or {}), "enabled": enabled}
    await write_config(d, FileConfig.model_validate(config))


async def plugin_list(request: web.Request) -> web.Response:
    d = dashboard(request)
    root = _root(request)
    enabled = (saved_config(d).get("addons") or {}).get("plugins") or {}
    running = {p.id for p in d.app.addons.plugins}
    updates = d.app.addons.updates
    rows = []
    for directory in sorted(root.iterdir()) if root.is_dir() else []:
        manifest = _manifest(directory)
        if manifest:
            active = bool((enabled.get(directory.name) or {}).get("enabled"))
            values = enabled.get(directory.name) or {}
            rows.append(
                {
                    **manifest,
                    "enabled": active,
                    "running": directory.name in running,
                    "git": _git_info(record)
                    if (record := updates.record(directory.name))
                    else None,
                    "values": {
                        f["key"]: values.get(f["key"], f["default"])
                        for f in manifest["settings"]
                        if f["type"] != "secret"
                    },
                }
            )
    return web.json_response(rows)


async def plugin_upload(request: web.Request) -> web.Response:
    reader = await request.multipart()
    archive, password = b"", ""
    async for part in reader:
        if part.name == "password":  # type: ignore[union-attr]
            password = await part.text()  # type: ignore[union-attr]
        elif getattr(part, "filename", None):
            archive = bytes(await part.read(decode=False))  # type: ignore[union-attr]
    _confirmed(request, password)
    manifest = await asyncio.to_thread(unpack, archive, _root(request))
    dashboard(request).app.db.audit(f"web:{current(request).username}", "plugin.install",
                                    f"{manifest['id']} {manifest['sha256'][:16]}")  # fmt: skip
    return web.json_response(manifest)


async def plugin_from_git(request: web.Request) -> web.Response:
    """Install from a public Git repository with a plugin.json at its top."""
    data = await read_json(request)
    _confirmed(request, str(data.get("password", "")))
    url = str(data.get("url", "")).strip()
    if not re.match(r"^https://[A-Za-z0-9./_-]{4,200}$", url):
        raise web.HTTPBadRequest(text="web.pm_bad_url")
    d = dashboard(request)
    try:
        manifest = await d.app.addons.updates.install(url)
    except PluginGitError as exc:
        status = web.HTTPBadGateway if exc.key == "web.pm_git_failed" else web.HTTPBadRequest
        raise status(text=exc.key) from None
    d.app.db.audit(f"web:{current(request).username}", "plugin.install", url[:120])
    return web.json_response(manifest)


async def plugin_check(request: web.Request) -> web.Response:
    d = dashboard(request)
    try:
        record = await d.app.addons.updates.check(request.match_info["id"])
    except PluginGitError as exc:
        raise web.HTTPBadGateway(text=exc.key) from None
    if record is None:
        raise web.HTTPNotFound()
    return web.json_response(_git_info(record))


async def plugin_update(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    _confirmed(request, str(data.get("password", "")))
    try:
        manifest = await d.app.addons.updates.update(request.match_info["id"])
    except PluginGitError as exc:
        raise web.HTTPBadGateway(text=exc.key) from None
    return web.json_response(manifest)


async def plugin_auto(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    enabled = data.get("enabled") is True
    if enabled:  # new code from elsewhere would run without anybody looking at it
        _confirmed(request, str(data.get("password", "")))
    if not d.app.addons.updates.set_auto(request.match_info["id"], enabled):
        raise web.HTTPNotFound()
    d.app.db.audit(f"web:{current(request).username}", "plugin.auto_update", str(enabled))
    return web.json_response({"ok": True})


def _git_info(record: dict[str, Any]) -> dict[str, Any]:
    return {"url": record["url"], "branch": record["branch"], "commit": record["commit"][:10],
            "auto": record["auto"], "update": record["latest"] != record["commit"]}  # fmt: skip


def _running_plugin(request: web.Request) -> Any:
    plugin_id = request.match_info["id"]
    plugin = next((p for p in dashboard(request).app.addons.plugins if p.id == plugin_id), None)
    if plugin is None:
        raise web.HTTPConflict(text="web.addon_not_running")
    return plugin


async def plugin_panel(request: web.Request) -> web.Response:
    return web.json_response(_running_plugin(request).panel())


async def plugin_action(request: web.Request) -> web.Response:
    plugin = _running_plugin(request)
    dashboard(request).app.db.audit(f"web:{current(request).username}", "plugin.action", plugin.id)
    return web.json_response({"message": await plugin.action(request.match_info["name"])})


async def plugin_settings(request: web.Request) -> web.Response:
    _, manifest = _plugin_dir(request)
    data = await read_json(request)
    known = {f["key"] for f in manifest["settings"]}
    d = dashboard(request)
    config = saved_config(d)
    plugins = config.setdefault("addons", {}).setdefault("plugins", {})
    current_values = plugins.get(manifest["id"]) or {}
    plugins[manifest["id"]] = {**current_values, **{k: v for k, v in data.items() if k in known}}
    await write_config(d, FileConfig.model_validate(config))
    return web.json_response({"ok": True, "restart_required": True})


async def plugin_create(request: web.Request) -> web.Response:
    data = await read_json(request)
    _confirmed(request, str(data.get("password", "")))
    manifest = _checked({"id": data.get("id"), "name": data.get("name"), "version": "0.1.0",
                         "entry": "plugin.py", "author": data.get("author", "")})  # fmt: skip
    directory = _root(request) / manifest["id"]
    if directory.exists():
        raise web.HTTPConflict(text="web.pm_exists")
    directory.mkdir(parents=True)
    (directory / "plugin.json").write_text(json.dumps(manifest, indent=2))
    class_name = "".join(p.title() for p in manifest["id"].split("_"))
    (directory / "plugin.py").write_text(TEMPLATE.format(cls=class_name, id=manifest["id"],
                                                         name=manifest["name"]))  # fmt: skip
    return web.json_response(manifest)


def _plugin_dir(request: web.Request) -> tuple[Path, dict[str, Any]]:
    plugin_id = request.match_info["id"]
    directory = _root(request) / plugin_id
    manifest = _manifest(directory) if _ID_RE.match(plugin_id) else None
    if manifest is None:
        raise web.HTTPNotFound()
    return directory, manifest


async def plugin_toggle(request: web.Request) -> web.Response:
    _, manifest = _plugin_dir(request)
    data = await read_json(request)
    await _set_enabled(request, manifest["id"], data.get("enabled") is True)
    return web.json_response({"ok": True, "restart_required": True})


async def plugin_source(request: web.Request) -> web.Response:
    directory, manifest = _plugin_dir(request)
    return web.json_response({"entry": manifest["entry"],
                              "source": (directory / manifest["entry"]).read_text()})  # fmt: skip


async def plugin_save_source(request: web.Request) -> web.Response:
    directory, manifest = _plugin_dir(request)
    data = await read_json(request)
    _confirmed(request, str(data.get("password", "")))
    source = str(data.get("source", ""))
    if len(source) > MAX_SOURCE:
        raise web.HTTPBadRequest(text="web.pm_too_long")
    try:
        compile(source, manifest["entry"], "exec")
    except SyntaxError as exc:
        raise web.HTTPBadRequest(text=f"Line {exc.lineno}: {exc.msg}") from None
    (directory / manifest["entry"]).write_text(source)
    dashboard(request).app.db.audit(
        f"web:{current(request).username}", "plugin.edit", manifest["id"]
    )
    return web.json_response({"ok": True, "restart_required": True})


async def plugin_delete(request: web.Request) -> web.Response:
    directory, manifest = _plugin_dir(request)
    await _set_enabled(request, manifest["id"], False)
    shutil.rmtree(directory)
    dashboard(request).app.addons.updates.forget(manifest["id"])
    dashboard(request).app.db.audit(
        f"web:{current(request).username}", "plugin.delete", manifest["id"]
    )
    return web.json_response({"ok": True})
