from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from pi_guard.core.events import Event, EventType
from pi_guard.core.modes import Mode
from pi_guard.messaging.commands.images import parse_region

from .helpers import ADMIN, VIEWER, camera, make_app, msg

cv2 = pytest.importorskip("cv2")
from pi_guard.cameras import imaging  # noqa: E402


def scene(dark: bool = False) -> np.ndarray:
    """Left half red, right half blue, with fine stripes so sharpness is measurable."""
    image = np.zeros((360, 640, 3), dtype=np.uint8)
    image[:, :320] = (0, 0, 200)
    image[:, 320:] = (200, 0, 0)
    image[::4, :] = 255
    return (image * 0.2).astype(np.uint8) if dark else image


def write(path: Path, image: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image)
    return path


def test_sharpest_frame_wins(tmp_path: Path) -> None:
    sharp = write(tmp_path / "sharp.jpg", scene())
    blurry = write(tmp_path / "blurry.jpg", cv2.GaussianBlur(scene(), (0, 0), 4))
    assert imaging.sharpness(imaging.load(sharp)) > imaging.sharpness(imaging.load(blurry))
    assert imaging.sharpest([blurry, sharp]) == sharp


@pytest.mark.parametrize("region", imaging.REGIONS)
def test_region_boxes_stay_inside_the_frame(region: str) -> None:
    x, y, w, h = imaging.region_box(640, 360, region)
    assert x >= 0 and y >= 0 and x + w <= 640 and y + h <= 360
    assert (w, h) == (320, 180)


def test_zoom_shows_the_chosen_side() -> None:
    right = imaging.zoom(scene(), "right")
    assert right.shape[1] >= 1280
    blue, red = right[..., 0].mean(), right[..., 2].mean()
    assert blue > red


def test_brighten_lifts_dark_pictures() -> None:
    dark = scene(dark=True)
    assert imaging.is_dark(dark)
    assert imaging.brighten(dark).mean() > dark.mean() * 1.3


def test_box_around_a_person() -> None:
    x, y, w, h = imaging.around((600, 300, 30, 40), 640, 360)
    assert x + w <= 640 and y + h <= 360 and w >= imaging.MIN_CROP_WIDTH
    assert x <= 600 and y <= 300


@pytest.mark.parametrize(
    ("text", "region"),
    [
        ("links", "left"),
        ("oben rechts", "top_right"),
        ("Oben-Links", "top_left"),
        ("5", "center"),
        ("↘", "bottom_right"),
        ("bottom_left", "bottom_left"),
        ("quer", None),
    ],
)
def test_region_names(text: str, region: str | None) -> None:
    assert parse_region(text) == region


# --- chat ---------------------------------------------------------------------------------


@pytest.fixture
def app(tmp_path: Path):  # type: ignore[no-untyped-def]
    application = make_app(tmp_path)
    write(application.cameras["living_room"].snapshot_path, scene())
    yield application
    application.db.close()


def last(app) -> Any:  # type: ignore[no-untyped-def]
    return app.providers["telegram"].last()


async def test_photo_then_zoom_and_brighten(app) -> None:  # type: ignore[no-untyped-def]
    await app.messenger.handle(msg("foto", VIEWER))
    original = last(app).photo
    commands = [b.command for row in last(app).buttons for b in row]
    assert {"zoom", "sharper living_room", "brighter"} <= set(commands)

    await app.messenger.handle(msg("vergrößern", VIEWER))
    assert len(last(app).buttons) == 4  # 3x3 grid and menu
    await app.messenger.handle(msg("zoom rechts", VIEWER))
    zoomed = last(app).photo
    assert zoomed != original and imaging.load(zoomed).shape[1] >= 1280

    await app.messenger.handle(msg("heller", VIEWER))
    assert last(app).photo not in (None, zoomed)


async def test_zoom_without_photo_takes_one(app) -> None:  # type: ignore[no-untyped-def]
    await app.messenger.handle(msg("/zoom mitte", ADMIN))
    assert last(app).photo is not None


async def test_sharper_frames_the_person(app, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    async def series(camera_id: str, count: int) -> list[Path]:
        return [await app.take_photo(camera_id, prefix="series") for _ in range(count)]

    class Locator:
        def locate(self, image: np.ndarray) -> tuple[int, int, int, int]:
            return (400, 100, 60, 80)

    monkeypatch.setattr(app, "photo_series", series)
    monkeypatch.setattr(app, "person_locator", lambda: Locator())
    await app.messenger.handle(msg("/schärfer", VIEWER))
    reply = last(app)
    assert "enlarged around the person" in reply.text
    assert imaging.load(reply.photo).shape[1] >= 1280
    assert not list((app.media_dir / "snapshots").glob("*series*"))  # series removed


async def test_photos_of_all_cameras(tmp_path: Path) -> None:
    app = make_app(tmp_path, cameras=[camera(), camera("entrance", "Entrance")])
    for pipeline in app.cameras.values():
        write(pipeline.snapshot_path, scene())
    await app.messenger.handle(msg("foto alle", VIEWER))
    sent = app.providers["telegram"].sent
    assert [r.photo is not None for _, r in sent] == [True, True]
    assert "Living Room" in sent[0][1].text and "Entrance" in sent[1][1].text
    app.db.close()


async def test_zoom_works_on_the_alert_photo(app) -> None:  # type: ignore[no-untyped-def]
    app.modes.set_manual(Mode.AWAY)
    snapshot = await app.take_photo("living_room", prefix="motion")
    await app.notifier.handle(
        Event(EventType.MOTION_DETECTED, camera="living_room", snapshot=snapshot)
    )
    assert app.last_photos[f"telegram:{VIEWER}"] == snapshot
    await app.messenger.handle(msg("zoom links", VIEWER))
    assert last(app).photo is not None
