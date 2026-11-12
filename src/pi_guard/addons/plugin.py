"""The plugin API: add commands, dashboard routes and background work without touching pi-guard.

    from pi_guard.addons.plugin import Plugin

    class Hello(Plugin):
        id = "hello"
        title = "Hello"

        def commands(self):
            return [self.command("hello", self.say_hello, aliases=("hallo",))]

        async def say_hello(self, ctx, args):
            return self.reply("Hello!")

A plugin is an installed Python package with an entry point in the group `pi_guard.plugins`,
a file in `/etc/pi-guard/plugins/` that belongs to root, or an add-on installed in the
dashboard (`<data dir>/plugins/<id>/` with a plugin.json). Plugins run with the rights of
pi-guard and can do what it can do; the dashboard asks for the password before it takes
code. Each plugin is switched on in `addons.plugins.<id>.enabled`, and the rest of that section
is its settings. A broken plugin is skipped, not fatal.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import re
import stat
from collections.abc import Awaitable, Callable, Iterable
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from pi_guard.core.events import Event
from pi_guard.core.permissions import Role
from pi_guard.messaging.base import Button, Reply
from pi_guard.messaging.commands.registry import Command, TextArg

if TYPE_CHECKING:
    from aiohttp import web

    from pi_guard.app import App

log = logging.getLogger(__name__)

PLUGIN_DIR = Path("/etc/pi-guard/plugins")
UPLOAD_DIR = "plugins"  # inside the data directory
MANIFESTS = ("plugin.json", "plugins.json")
API_VERSION = 1
_PLUGIN_ID = re.compile(r"^[a-z0-9_]{2,32}$")
RESERVED = {"heartbeat", "analytics", "smarthome", "remote", "assistant", "agents", "languages"}


class ManifestError(ValueError):
    pass


def read_manifest(directory: Path) -> dict[str, Any]:
    for name in MANIFESTS:
        path = directory / name
        if path.exists():
            return check_manifest(json.loads(path.read_text()))
    raise ManifestError("no plugin.json")


def check_manifest(data: Any) -> dict[str, Any]:
    """The shape of a plugin.json; anything else is left out."""
    if not isinstance(data, dict):
        raise ManifestError("not an object")
    plugin_id = str(data.get("id", ""))
    entry = str(data.get("entry", "plugin.py"))
    if not _PLUGIN_ID.match(plugin_id) or plugin_id in RESERVED:
        raise ManifestError("id: 2-32 of a-z, 0-9, _ (and not a built-in add-on)")
    if not data.get("name"):
        raise ManifestError("name is required")
    if not re.match(r"^[A-Za-z0-9_]+\.py$", entry):
        raise ManifestError("entry must be a Python file in the plugin folder")
    if int(data.get("api", API_VERSION)) > API_VERSION:
        raise ManifestError("needs a newer pi-guard")
    authors = data.get("authors") or data.get("author") or []
    authors = [authors] if isinstance(authors, str) else list(authors)
    source = str(data.get("source", ""))
    if source and not source.startswith("https://"):
        raise ManifestError("source must be an https:// link")
    branch = str(data.get("release_branch", ""))
    if branch and not re.match(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$", branch):
        raise ManifestError("release_branch is not a branch name")
    docs = str(data.get("docs", "") or "")
    if docs and not docs.startswith("https://"):
        raise ManifestError("docs must be an https:// link")
    fields = []
    for item in data.get("settings") or []:
        if isinstance(item, dict) and re.match(r"^[a-z0-9_]{1,32}$", str(item.get("key", ""))):
            fields.append({"key": item["key"], "label": str(item.get("label", item["key"]))[:80],
                           "type": item.get("type") if item.get("type") in (
                               "text", "number", "bool", "secret") else "text",
                           "default": item.get("default")})  # fmt: skip
    return {
        "id": plugin_id, "name": str(data["name"])[:60], "entry": entry,
        "version": str(data.get("version", "0"))[:20], "icon": str(data.get("icon", "cpu"))[:40],
        "authors": [str(a)[:60] for a in authors if a][:10], "source": source[:300],
        "description": str(data.get("description", ""))[:300],
        # What it does and how to use it, as Markdown: shown behind "?" on its card.
        "help": str(data.get("help", ""))[:4000],
        "docs": docs[:300],
        "license": str(data.get("license", ""))[:40], "settings": fields,
        "release_branch": branch,
        "permissions": [str(p)[:40] for p in data.get("permissions") or []][:20],
    }  # fmt: skip


TEMPLATE = '''"""{name}: an own pi-guard add-on."""

from pi_guard.addons.plugin import Plugin


class {cls}(Plugin):
    id = "{id}"
    title = "{name}"

    def commands(self):
        return [self.command("{id}", self.answer)]

    async def answer(self, ctx, args):
        return self.reply("Hello from {name}!")
'''
ENTRY_POINT_GROUP = "pi_guard.plugins"


class Plugin:
    id: ClassVar[str] = ""
    title: ClassVar[str] = ""
    description: ClassVar[str] = ""

    def __init__(self, app: App, settings: dict[str, Any]) -> None:
        self.app = app
        self.settings = settings

    # Override what you need.

    def commands(self) -> Iterable[Command]:
        """Chat commands; they work in every messenger."""
        return ()

    def routes(self) -> Iterable[web.RouteDef]:
        """Dashboard API, served below /api/plugins/<id>/ and only after login."""
        return ()

    async def run(self) -> None:
        """Background work; restarted with a growing pause if it raises."""
        await asyncio.Event().wait()

    def events(self) -> Iterable[str]:
        """Names of events to receive in on_event, e.g. "motion_detected"."""
        return ()

    async def on_event(self, event: Event) -> None:
        """Called for each event named in events()."""

    def panel(self) -> list[dict[str, Any]]:
        """What the add-on page shows; no HTML, so a plugin cannot reach the dashboard's session.

        Widgets: {"type": "text", "text": ...}, {"type": "stat", "label": ..., "value": ...},
        {"type": "list", "items": [...]}, {"type": "button", "label": ..., "action": ...}.
        """
        return []

    async def action(self, name: str) -> str | None:
        """A button of the panel was pressed; the text is shown as a message."""
        return None

    # Helpers.

    def command(
        self,
        name: str,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Reply]],
        aliases: tuple[str, ...] = (),
        admin_only: bool = True,
    ) -> Command:
        role = Role.ADMIN if admin_only else Role.VIEWER
        return Command(name, handler, aliases, (TextArg("text"),), role=role, menu=False)

    @staticmethod
    def reply(text: str, *buttons: tuple[str, str]) -> Reply:
        """`buttons`: (label, command) pairs."""
        return Reply(text, buttons=tuple((Button(a, b),) for a, b in buttons))

    def log(self, text: str) -> None:
        """An entry in the audit log."""
        self.app.db.audit(f"plugin:{self.id}", "plugin.message", text[:200])

    async def notify(self, text: str) -> None:
        """Send a message to all admins, in every connected messenger."""
        for user in self.app.users.all():
            if user.role is Role.ADMIN and user.provider in self.app.providers:
                await self.app.messenger.send(user.provider, user.user_id, Reply(text[:2000]))

    def load(self, key: str, default: Any = None) -> Any:
        """Stored values of this plugin survive restarts."""
        value = self.app.db.get(f"plugin.{self.id}.{key}")
        return default if value is None else value

    def save(self, key: str, value: Any) -> None:
        self.app.db.set(f"plugin.{self.id}.{key}", value)


def _trusted(path: Path) -> bool:
    info = path.stat()
    return info.st_uid == 0 and not info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)


def _from_file(path: Path) -> list[type[Plugin]]:
    spec = importlib.util.spec_from_file_location(f"pi_guard_plugin_{path.stem}", path)
    if spec is None or spec.loader is None:
        return []
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return [
        value for value in vars(module).values()
        if isinstance(value, type) and issubclass(value, Plugin) and value is not Plugin
    ]  # fmt: skip


def _uploaded(root: Path) -> list[type[Plugin]]:
    found: list[type[Plugin]] = []
    for directory in sorted(p for p in root.glob("*") if p.is_dir()):
        try:
            found += _from_file(directory / read_manifest(directory)["entry"])
        except Exception:
            log.exception("add-on %s could not be loaded", directory.name)
    return found


def discover(
    directory: Path = PLUGIN_DIR, check_owner: bool = True, uploads: Path | None = None
) -> dict[str, type[Plugin]]:
    found: list[type[Plugin]] = _uploaded(uploads) if uploads else []
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        try:
            found.append(entry.load())
        except Exception:
            log.exception("plugin %s could not be loaded", entry.name)
    if directory.is_dir():
        for path in sorted(directory.glob("*.py")):
            if check_owner and not _trusted(path):
                log.warning("ignoring plugin %s: it must belong to root", path.name)
                continue
            try:
                found += _from_file(path)
            except Exception:
                log.exception("plugin %s could not be loaded", path.name)
    return {plugin.id: plugin for plugin in found if plugin.id}


def load_plugins(app: App, settings: dict[str, dict[str, Any]]) -> list[Plugin]:
    wanted = {k: v for k, v in settings.items() if v.get("enabled")}
    if not wanted:
        return []
    plugins: list[Plugin] = []
    available = discover(uploads=app.settings.data_dir / UPLOAD_DIR)
    for plugin_id, config in wanted.items():
        cls = available.get(plugin_id)
        if cls is None:
            log.warning("plugin %s is switched on but not installed", plugin_id)
            continue
        try:
            plugin = cls(app, config)
            for command in plugin.commands():
                app.registry.add(command)
            plugins.append(plugin)
            log.info("plugin %s loaded", plugin_id)
        except Exception:
            log.exception("plugin %s failed to start", plugin_id)
    return plugins
