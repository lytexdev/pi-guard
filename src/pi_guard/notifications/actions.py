"""What a tap in a notification can do: everything is fine, or sound the alarm.

Both come from the phone app (with its device key) and from browser push notifications (with
a token that came in that notification). Only admins get them, as in the dashboard.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pi_guard.audio.service import AudioError

if TYPE_CHECKING:
    from pi_guard.app import App

ACTIONS = ("ack", "alarm")
# Quick actions of the phone app: more than a notification button may do, so not for push.
QUICK = (*ACTIONS, "alarm_stop", "home", "away")
ALARM_SECONDS = 30  # as the alarm button in the dashboard


class ActionError(Exception):
    """`key` is a translation key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def run(app: App, actor: str, action: str) -> dict[str, int | bool]:
    if action == "ack":
        return {"acknowledged": app.escalation.acknowledge(actor)}
    if action == "alarm":
        if app.audio is None:
            raise ActionError("audio.playback_disabled")
        try:
            app.audio.start_alarm(ALARM_SECONDS)
        except AudioError as exc:
            raise ActionError(exc.key) from None
        app.db.audit(actor, "alarm.start")
        return {"alarm": True}
    raise ActionError("web.unknown_action")


async def quick(app: App, actor: str, action: str) -> dict[str, int | bool | str]:
    """A quick action from the phone app, by an admin."""
    from pi_guard.core.modes import Mode

    if action in ACTIONS:
        return dict(run(app, actor, action))
    if action == "alarm_stop":
        stopped = await app.audio.stop_alarm() if app.audio is not None else False
        app.db.audit(actor, "alarm.stop")
        return {"stopped": stopped}
    if action in ("home", "away"):
        app.modes.set_manual(Mode(action.upper()), actor=actor)
        return {"mode": action.upper()}
    raise ActionError("web.unknown_action")
