"""A linked household as a way to reach someone: contacts, the second line, emergency
contacts and SOS can name a household link ("household", the link's id) like a messenger.
The words go through the link and reach the other household's admins on their messengers;
pictures, sound and files stay here.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from pi_guard.messaging.base import MessageHandler, ProviderUnavailableError, Reply

if TYPE_CHECKING:
    from pi_guard.app import App
    from pi_guard.federation.service import Federation


class HouseholdProvider:
    name = "household"
    supports_buttons = False

    def __init__(self, app: App) -> None:
        self._app = app

    def _federation(self) -> Federation | None:
        dashboard = self._app.dashboard
        return getattr(dashboard, "federation", None) if dashboard is not None else None

    @property
    def connected(self) -> bool:
        federation = self._federation()
        return bool(federation and (federation.peers or federation.connected))

    async def run(self, on_message: MessageHandler) -> None:
        """Notes arrive through the federation's own connections, not here."""
        await asyncio.Event().wait()

    async def send(self, chat_id: str, reply: Reply) -> None:
        federation = self._federation()
        if federation is None or not chat_id.isdigit():
            raise ProviderUnavailableError("linked households are off")
        await federation.note(int(chat_id), reply.text, urgent=reply.tone == "alert")
