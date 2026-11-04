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
import stat
from collections.abc import Awaitable, Callable, Iterable
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from pi_guard.core.permissions import Role
from pi_guard.messaging.base import Button, Reply
from pi_guard.messaging.commands.registry import Command, TextArg

if TYPE_CHECKING:
    from aiohttp import web

    from pi_guard.app import App

log = logging.getLogger(__name__)

PLUGIN_DIR = Path("/etc/pi-guard/plugins")
UPLOAD_DIR = "plugins"  # inside the data directory
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

    def notify(self, text: str) -> None:
        """Log an entry in the event list of the dashboard."""
        self.app.db.audit(f"plugin:{self.id}", "plugin.message", text[:200])


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
    for manifest in sorted(root.glob("*/plugin.json")):
        try:
            entry = str(json.loads(manifest.read_text()).get("entry", "plugin.py"))
            found += _from_file(manifest.parent / Path(entry).name)
        except Exception:
            log.exception("add-on %s could not be loaded", manifest.parent.name)
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
