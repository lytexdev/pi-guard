"""Run fixed commands on other computers, e.g. shut down a PC so its disk is locked.

Only actions an admin configured beforehand can run; nothing typed in a chat ever
reaches a shell. Every run needs two separate confirmations by the same person.
Connections use a key pair of their own (`<data dir>/remote`). The other computer
can restrict that key to one command, see docs/addons.md.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from pi_guard.config.files import RemoteAction
from pi_guard.security.files import secure_dir

log = logging.getLogger(__name__)

CONFIRM_SECONDS = 60
# A double tap must not pass both confirmations.
MIN_SECONDS_BETWEEN_STEPS = 1.0
RUN_TIMEOUT = 45
OUTPUT_LIMIT = 400
SSH_CONNECTION_LOST = 255


class ConfirmationError(Exception):
    """`key` is a translation key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


@dataclass
class _Pending:
    action: RemoteAction
    actor: str
    step: int
    created: float


@dataclass(frozen=True)
class RunResult:
    ok: bool
    exit_code: int | None
    output: str
    # Shutting down often ends the connection before an exit code arrives.
    connection_ended: bool = False


class RemoteActions:
    def __init__(self, actions: list[RemoteAction], data_dir: Path) -> None:
        self.actions = {a.id: a for a in actions if a.enabled}
        self._dir = data_dir / "remote"
        self._key = self._dir / "id_ed25519"
        self._pending: dict[str, _Pending] = {}

    # --- key ----------------------------------------------------------------------------

    async def public_key(self) -> str:
        secure_dir(self._dir)
        if not self._key.exists():
            process = await asyncio.create_subprocess_exec(
                "ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "pi-guard", "-f",
                str(self._key), stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )  # fmt: skip
            if await process.wait() != 0:
                raise OSError("ssh-keygen failed")
        return self._key.with_suffix(".pub").read_text().strip()

    # --- confirmation -------------------------------------------------------------------

    def _expire(self) -> None:
        limit = time.monotonic() - CONFIRM_SECONDS
        self._pending = {k: p for k, p in self._pending.items() if p.created > limit}

    def begin(self, action_id: str, actor: str) -> str:
        """First step: returns the token for the first confirmation."""
        self._expire()
        action = self.actions.get(action_id)
        if action is None:
            raise ConfirmationError("remote.unknown")
        return self._issue(action, actor, 1)

    def _issue(self, action: RemoteAction, actor: str, step: int) -> str:
        token = secrets.token_hex(6)
        self._pending[token] = _Pending(action, actor, step, time.monotonic())
        return token

    def confirm(self, token: str, actor: str) -> tuple[RemoteAction, str | None]:
        """(action, token for the second confirmation), or (action, None) when fully confirmed."""
        self._expire()
        pending = self._pending.pop(token, None)
        if pending is None or pending.actor != actor:
            raise ConfirmationError("remote.expired")
        if time.monotonic() - pending.created < MIN_SECONDS_BETWEEN_STEPS:
            raise ConfirmationError("remote.too_fast")
        if pending.step == 1:
            return pending.action, self._issue(pending.action, actor, 2)
        return pending.action, None

    def cancel(self, token: str) -> None:
        self._pending.pop(token, None)

    # --- running ------------------------------------------------------------------------

    def command_line(self, action: RemoteAction) -> list[str]:
        return [
            "ssh", "-F", "/dev/null", "-i", str(self._key),
            "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=10",
            "-o", "LogLevel=ERROR",
            # The first connection pins the computer's key; a changed key is refused.
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", f"UserKnownHostsFile={self._dir / 'known_hosts'}",
            "-p", str(action.port), "-l", action.user, "--", action.host, action.command,
        ]  # fmt: skip

    async def run(self, action: RemoteAction) -> RunResult:
        await self.public_key()
        process = await asyncio.create_subprocess_exec(
            *self.command_line(action),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), RUN_TIMEOUT)
        except TimeoutError:
            process.kill()
            await process.wait()
            return RunResult(False, None, "timeout")
        text = output.decode(errors="replace").strip()[-OUTPUT_LIMIT:]
        code = process.returncode
        ended = code == SSH_CONNECTION_LOST and "closed" in text.casefold()
        log.info("remote action %s finished with exit code %s", action.id, code)
        return RunResult(code == 0 or ended, code, text, ended)
