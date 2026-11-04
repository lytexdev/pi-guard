"""Optional add-ons, each switched on in `addons:` of the configuration.

Every add-on works the same way everywhere: the dashboard (web/api_addons.py) and all
chat providers (messaging/commands/addons.py) use the classes in this package.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from pi_guard.addons.plugin import load_plugins

if TYPE_CHECKING:
    from pi_guard.addons.analytics import NetworkAnalytics
    from pi_guard.addons.assistant import Assistant
    from pi_guard.addons.blocking import Blocks
    from pi_guard.addons.commander import Commander
    from pi_guard.addons.heartbeat import HeartbeatWatcher
    from pi_guard.addons.jobs import Jobs
    from pi_guard.addons.remote import RemoteActions
    from pi_guard.addons.smarthome import SmartHome
    from pi_guard.app import App
    from pi_guard.config.files import AssistantAddon
    from pi_guard.config.settings import Settings


class Addons:
    """The add-ons that are switched on; attributes stay None otherwise."""

    def __init__(self, app: App) -> None:
        settings = app.config.file.addons
        s = app.settings
        self.heartbeat: HeartbeatWatcher | None = None
        self.analytics: NetworkAnalytics | None = None
        self.smarthome: SmartHome | None = None
        self.blocks: Blocks | None = None
        self.remote: RemoteActions | None = None
        self.assistant: Assistant | None = None
        if settings.heartbeat.enabled:
            from pi_guard.addons.heartbeat import HeartbeatWatcher

            self.heartbeat = HeartbeatWatcher(settings.heartbeat.targets, app.bus, app.db)
        if settings.analytics.enabled and app.pihole is not None:
            from pi_guard.addons.analytics import NetworkAnalytics
            from pi_guard.addons.blocking import Blocks

            self.analytics = NetworkAnalytics(app.pihole)
            self.blocks = Blocks(app.db, app.pihole, s.blockpage_ip)
        if settings.smarthome.enabled:
            from pi_guard.addons.smarthome import SmartHome

            self.smarthome = SmartHome(app)
        if settings.remote.enabled:
            from pi_guard.addons.remote import RemoteActions

            self.remote = RemoteActions(settings.remote.actions, s.data_dir)
        if settings.assistant.enabled:
            from pi_guard.addons.assistant import Assistant
            from pi_guard.addons.knowledge import Knowledge

            api_key = s.assistant_api_key.get_secret_value() if s.assistant_api_key else None
            deepl = s.assistant_deepl_key.get_secret_value() if s.assistant_deepl_key else None
            self.assistant = Assistant(
                settings.assistant, app.db, s.tz, api_key or None, deepl or None, app.media_dir,
                Knowledge(s.data_dir / "knowledge.db"), _searchers(settings.assistant, s),
            )  # fmt: skip
        self.jobs: Jobs | None = None
        self.commander: Commander | None = None
        if self.assistant is not None:
            from pi_guard.addons.commander import Commander
            from pi_guard.addons.jobs import Jobs

            self.jobs = Jobs(app)
            self.commander = Commander(app)
        self.plugins = load_plugins(app, settings.plugins)

    def components(self) -> dict[str, Callable[[], Awaitable[None]]]:
        parts: dict[str, Callable[[], Awaitable[None]]] = {}
        if self.heartbeat is not None:
            parts["addon:heartbeat"] = self.heartbeat.run
        if self.smarthome is not None:
            parts["addon:smarthome"] = self.smarthome.run
        if self.blocks is not None:
            parts["addon:blocks"] = self.blocks.run
        if self.jobs is not None:
            parts["addon:jobs"] = self.jobs.run
        for plugin in self.plugins:
            parts[f"plugin:{plugin.id}"] = plugin.run
        return parts


def _searchers(config: AssistantAddon, s: Settings) -> tuple[Any, ...]:
    from pi_guard.addons.documents import Nextcloud, Paperless

    found: list[Any] = []
    token = s.assistant_paperless_token
    if config.paperless_url and token:
        found.append(Paperless(config.paperless_url, token.get_secret_value()))
    password = s.assistant_nextcloud_password
    if config.nextcloud_url and config.nextcloud_user and password:
        found.append(Nextcloud(config.nextcloud_url, config.nextcloud_user,
                               password.get_secret_value()))  # fmt: skip
    return tuple(found)
