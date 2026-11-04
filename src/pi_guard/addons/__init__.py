"""Optional add-ons, each switched on in `addons:` of the configuration.

Every add-on works the same way everywhere: the dashboard (web/api_addons.py) and all
chat providers (messaging/commands/addons.py) use the classes in this package.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pi_guard.addons.analytics import NetworkAnalytics
    from pi_guard.addons.assistant import Assistant
    from pi_guard.addons.heartbeat import HeartbeatWatcher
    from pi_guard.addons.remote import RemoteActions
    from pi_guard.addons.smarthome import SmartHome
    from pi_guard.app import App


class Addons:
    """The add-ons that are switched on; attributes stay None otherwise."""

    def __init__(self, app: App) -> None:
        settings = app.config.file.addons
        s = app.settings
        self.heartbeat: HeartbeatWatcher | None = None
        self.analytics: NetworkAnalytics | None = None
        self.smarthome: SmartHome | None = None
        self.remote: RemoteActions | None = None
        self.assistant: Assistant | None = None
        if settings.heartbeat.enabled:
            from pi_guard.addons.heartbeat import HeartbeatWatcher

            self.heartbeat = HeartbeatWatcher(settings.heartbeat.targets, app.bus, app.db)
        if settings.analytics.enabled and app.pihole is not None:
            from pi_guard.addons.analytics import NetworkAnalytics

            self.analytics = NetworkAnalytics(app.pihole)
        if settings.smarthome.enabled:
            from pi_guard.addons.smarthome import SmartHome

            self.smarthome = SmartHome(app)
        if settings.remote.enabled:
            from pi_guard.addons.remote import RemoteActions

            self.remote = RemoteActions(settings.remote.actions, s.data_dir)
        if settings.assistant.enabled:
            from pi_guard.addons.assistant import Assistant

            api_key = s.assistant_api_key.get_secret_value() if s.assistant_api_key else None
            deepl = s.assistant_deepl_key.get_secret_value() if s.assistant_deepl_key else None
            self.assistant = Assistant(
                settings.assistant, app.db, s.tz, api_key or None, deepl or None, app.media_dir
            )

    def components(self) -> dict[str, Callable[[], Awaitable[None]]]:
        parts: dict[str, Callable[[], Awaitable[None]]] = {}
        if self.heartbeat is not None:
            parts["addon:heartbeat"] = self.heartbeat.run
        if self.smarthome is not None:
            parts["addon:smarthome"] = self.smarthome.run
        return parts
