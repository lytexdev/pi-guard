"""Better pictures on request: sharper, zoomed in, brighter.

`/sharper` takes a few pictures in a row, keeps the one with the least blur
and, if a person is visible, enlarges the area around them. `/zoom` and
`/brighter` work on the last photo this chat received (a requested photo or an
alert), so "zoom into that" means what it says.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from pi_guard.cameras import imaging
from pi_guard.cameras.pipeline import SnapshotUnavailableError
from pi_guard.core.timeutil import utcnow
from pi_guard.detection.persons import PersonDetector
from pi_guard.messaging.base import Button, Reply
from pi_guard.messaging.commands.context import CommandContext
from pi_guard.messaging.commands.core import menu_row, photo_buttons
from pi_guard.messaging.commands.registry import CameraArg, Command, Registry, TextArg

SERIES_LENGTH = 3

_REGION_NAMES = {
    "top_left": ("obenlinks", "topleft", "1", "↖"),
    "top": ("oben", "top", "up", "2", "↑"),
    "top_right": ("obenrechts", "topright", "3", "↗"),
    "left": ("links", "left", "4", "←"),
    "center": ("mitte", "center", "centre", "middle", "5", "·"),
    "right": ("rechts", "right", "6", "→"),
    "bottom_left": ("untenlinks", "bottomleft", "7", "↙"),
    "bottom": ("unten", "bottom", "down", "8", "↓"),
    "bottom_right": ("untenrechts", "bottomright", "9", "↘"),
}
_REGIONS = {alias: region for region, aliases in _REGION_NAMES.items() for alias in aliases}
_ARROWS = ("↖", "↑", "↗", "←", "·", "→", "↙", "↓", "↘")


def parse_region(text: str) -> str | None:
    key = "".join(c for c in text.casefold() if c not in " -_")
    return _REGIONS.get(key) or (key if key in imaging.REGIONS else None)


def _output(ctx: CommandContext, kind: str) -> Path:
    return ctx.app.media_dir / "snapshots" / f"{kind}_{utcnow():%Y%m%dT%H%M%S%fZ}.jpg"


async def _source(ctx: CommandContext) -> Path | None:
    """The chat's last photo, or a fresh one when there is none."""
    path = ctx.app.last_photos.get(ctx.chat_key)
    if path is not None and path.is_file():
        return path
    pipeline = ctx.camera({})
    if pipeline is None:
        return None
    try:
        return await ctx.app.take_photo(pipeline.camera.id)
    except SnapshotUnavailableError:
        return None


def _reply(ctx: CommandContext, image: imaging.Image, kind: str, text: str,
           extra: tuple[tuple[Button, ...], ...] = ()) -> Reply:  # fmt: skip
    path = imaging.save(image, _output(ctx, kind))
    ctx.app.last_photos[ctx.chat_key] = path
    return Reply(text, photo=path, buttons=(*extra, menu_row(ctx)))


async def sharper(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    pipeline = ctx.camera(args)
    if pipeline is None:
        return Reply(ctx.t("camera.choose"), buttons=(*ctx.camera_choice("sharper"), menu_row(ctx)))
    await ctx.progress(ctx.t("image.working"))
    try:
        series = await ctx.app.photo_series(pipeline.camera.id, SERIES_LENGTH)
    except SnapshotUnavailableError:
        return Reply(ctx.t("photo.unavailable", name=pipeline.camera.name),
                     buttons=(menu_row(ctx),))  # fmt: skip
    try:
        image, person = await asyncio.to_thread(_sharpest, series, ctx.app.person_locator())
    finally:
        for path in series:
            path.unlink(missing_ok=True)
    key = "image.sharper_person" if person else "image.sharper"
    return _reply(ctx, image, f"{pipeline.camera.id}_sharp", ctx.t(key, name=pipeline.camera.name),
                  photo_buttons(ctx, pipeline.camera.id)[:-1])  # fmt: skip


def _sharpest(series: list[Path], locator: PersonDetector | None) -> tuple[imaging.Image, bool]:
    image = imaging.load(imaging.sharpest(series))
    box = locator.locate(image) if locator else None
    if box is not None:
        height, width = image.shape[:2]
        image = imaging.enlarge(imaging.crop(image, imaging.around(box, width, height)))
    image = imaging.sharpen(image)
    if imaging.is_dark(image):
        image = imaging.brighten(image)
    return image, box is not None


def _zoom_grid(ctx: CommandContext) -> tuple[tuple[Button, ...], ...]:
    regions = list(_REGION_NAMES)
    return tuple(
        tuple(Button(_ARROWS[i], f"zoom {regions[i]}") for i in range(row * 3, row * 3 + 3))
        for row in range(3)
    )


async def zoom(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    text = args.get("region", "")
    region = parse_region(text) if text else None
    if text and region is None:
        return Reply(ctx.t("image.zoom_help"), buttons=(*_zoom_grid(ctx), menu_row(ctx)))
    source = await _source(ctx)
    if source is None:
        return Reply(ctx.t("image.no_photo"), buttons=(menu_row(ctx),))
    if region is None:
        return Reply(ctx.t("image.zoom_choose"), buttons=(*_zoom_grid(ctx), menu_row(ctx)))
    image = await asyncio.to_thread(lambda: imaging.zoom(imaging.load(source), region))
    return _reply(ctx, image, "zoom", ctx.t("image.zoomed"), (
        (ctx.button("image.zoom_more", "zoom"), ctx.button("image.brighter_button", "brighter")),
        (ctx.button("photo.again", "photo"),),
    ))  # fmt: skip


async def brighter(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    source = await _source(ctx)
    if source is None:
        return Reply(ctx.t("image.no_photo"), buttons=(menu_row(ctx),))
    image = await asyncio.to_thread(lambda: imaging.brighten(imaging.load(source)))
    return _reply(ctx, image, "bright", ctx.t("image.brightened"), (
        (ctx.button("image.zoom_button", "zoom"), ctx.button("photo.again", "photo")),
    ))  # fmt: skip


def register(registry: Registry) -> None:
    registry.add(Command("sharper", sharper,
                         ("schärfer", "schaerfer", "scharf", "deutlicher", "sharpen"),
                         (CameraArg(),), help_key="help.sharper"))  # fmt: skip
    registry.add(Command("zoom", zoom, ("vergrößern", "vergroessern", "näher", "naeher", "zoomen"),
                         (TextArg("region"),), help_key="help.zoom"))  # fmt: skip
    registry.add(Command("brighter", brighter, ("heller", "aufhellen", "hell", "brighten"),
                         help_key="help.brighter"))  # fmt: skip
