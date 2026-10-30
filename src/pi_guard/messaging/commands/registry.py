"""Central command registry and parser.

Every command is registered once with all its aliases (English, German,
short forms). `/v 30`, `video 30` and `/Video 30` therefore reach the same
handler with the same parsed arguments.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Protocol

from pi_guard.core.permissions import Role
from pi_guard.core.timeutil import parse_duration

if TYPE_CHECKING:
    from pi_guard.messaging.base import Reply
    from pi_guard.messaging.commands.context import CommandContext

_TELEGRAM_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")


class ArgError(Exception):
    """Invalid user input; `key` is a translation key, `values` its placeholders."""

    def __init__(self, key: str, **values: object) -> None:
        super().__init__(key)
        self.key = key
        self.values = values


class Arg(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def optional(self) -> bool: ...

    def parse(self, token: str, ctx: CommandContext) -> Any | None:
        """Return the parsed value, or None if the token does not fit this argument."""


@dataclass(frozen=True)
class IntArg:
    name: str
    minimum: int
    maximum: int
    optional: bool = True

    def parse(self, token: str, ctx: CommandContext) -> int | None:
        if not token.isdigit():
            return None
        value = int(token)
        if not self.minimum <= value <= self.maximum:
            raise ArgError("error.range", minimum=self.minimum, maximum=self.maximum)
        return value


@dataclass(frozen=True)
class ChoiceArg:
    name: str
    choices: dict[str, str]  # alias -> canonical value
    optional: bool = True

    def parse(self, token: str, ctx: CommandContext) -> str | None:
        return self.choices.get(token)


@dataclass(frozen=True)
class DurationArg:
    name: str
    maximum: timedelta = timedelta(days=30)
    optional: bool = True

    def parse(self, token: str, ctx: CommandContext) -> timedelta | None:
        try:
            value = parse_duration(token)
        except ValueError:
            return None
        if value > self.maximum:
            raise ArgError("error.duration_too_long")
        return value


@dataclass(frozen=True)
class CameraArg:
    """Matches a camera by id or name; unique prefixes are accepted."""

    name: str = "camera"
    optional: bool = True

    def parse(self, token: str, ctx: CommandContext) -> str | None:
        cameras = ctx.camera_names()
        for camera_id, name in cameras.items():
            if token in (camera_id, normalize(name).replace(" ", "_")):
                return camera_id
        matches = [
            cid
            for cid, name in cameras.items()
            if cid.startswith(token) or normalize(name).startswith(token)
        ]
        if len(matches) > 1:
            raise ArgError("error.camera_ambiguous", options=", ".join(sorted(matches)))
        return matches[0] if matches else None


@dataclass(frozen=True)
class TextArg:
    """Consumes the remaining tokens as free text."""

    name: str
    optional: bool = True

    def parse(self, token: str, ctx: CommandContext) -> str | None:
        return token or None


Handler = Callable[["CommandContext", dict[str, Any]], Awaitable["Reply"]]


@dataclass(frozen=True)
class Command:
    name: str
    handler: Handler
    aliases: tuple[str, ...] = ()
    args: tuple[Arg, ...] = ()
    role: Role = Role.VIEWER
    help_key: str | None = None
    menu: bool = True  # registered as a native Telegram slash command

    @property
    def all_names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass(frozen=True)
class ParsedCommand:
    command: Command
    tokens: tuple[str, ...]
    raw_args: str


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip().casefold()


class Registry:
    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}
        self._aliases: dict[str, Command] = {}

    def add(self, command: Command) -> Command:
        if not _TELEGRAM_NAME_RE.match(command.name):
            raise ValueError(f"command name {command.name!r} is not valid for Telegram")
        for alias in command.all_names:
            key = normalize(alias)
            if not key or " " in key:
                raise ValueError(f"invalid alias {alias!r}")
            if key in self._aliases:
                raise ValueError(
                    f"alias {alias!r} of {command.name} collides with {self._aliases[key].name}"
                )
            self._aliases[key] = command
        self._commands[command.name] = command
        return command

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._commands.values())

    def get(self, name: str) -> Command | None:
        return self._commands.get(name)

    def parse(self, text: str, bot_username: str | None = None) -> ParsedCommand | None:
        """Return the command for a message, or None if it is not a command at all."""
        text = normalize(text)
        if not text:
            return None
        head, _, rest = text.partition(" ")
        if head.startswith("/"):
            head, _, target = head[1:].partition("@")
            if target and bot_username and target != normalize(bot_username):
                return None  # addressed to another bot in a group
        command = self._aliases.get(head)
        if command is None:
            return None
        return ParsedCommand(command, tuple(rest.split()), rest.strip())


def bind_args(args: Sequence[Arg], tokens: Sequence[str], ctx: CommandContext) -> dict[str, Any]:
    """Assign tokens to arguments in order; optional arguments may be skipped."""
    values: dict[str, Any] = {}
    index = 0
    for arg in args:
        if index >= len(tokens):
            break
        if isinstance(arg, TextArg):
            values[arg.name] = " ".join(tokens[index:])
            index = len(tokens)
            break
        value = arg.parse(tokens[index], ctx)
        if value is not None:
            values[arg.name] = value
            index += 1
        elif not arg.optional:
            raise ArgError("error.bad_argument", value=tokens[index])
    if index < len(tokens):
        raise ArgError("error.bad_argument", value=tokens[index])
    for arg in args:
        if not arg.optional and arg.name not in values:
            raise ArgError("error.missing_argument")
    return values
