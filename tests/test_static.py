from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "src" / "pi_guard" / "web" / "static"


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
@pytest.mark.parametrize("path", sorted(STATIC.rglob("*.js")), ids=lambda p: p.name)
def test_dashboard_scripts_parse(path: Path, tmp_path: Path) -> None:
    # A syntax error in one view breaks the whole page in the browser.
    module = tmp_path / (path.stem + (".js" if path.name == "sw.js" else ".mjs"))
    module.write_text(path.read_text())
    result = subprocess.run(["node", "--check", str(module)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_every_text_used_by_the_dashboard_reaches_the_browser() -> None:
    # A key missing from the browser's catalog shows up as raw "addons.heartbeat" on the page.
    import inspect
    import re

    from pi_guard.i18n import catalog
    from pi_guard.web import api_core

    prefixes = re.search(r"startswith\(\((.*?)\)\)", inspect.getsource(api_core.i18n), re.S)
    assert prefixes is not None
    sent = tuple(re.findall(r'"([^"]+)"', prefixes.group(1)))
    keys = set(catalog("de"))
    for script in STATIC.rglob("*.js"):
        for key in re.findall(r'\bt\("([a-z_]+\.[a-z0-9_.]+)"', script.read_text()):
            assert key in keys, f"{script.name}: no text for {key}"
            assert key.startswith(sent), f"{script.name}: {key} is not sent to the browser"


def test_every_icon_used_is_in_the_sprite() -> None:
    static = Path(__file__).resolve().parent.parent / "src" / "pi_guard" / "web" / "static"
    sprite = (static / "icons" / "sprite.svg").read_text()
    known = set(re.findall(r'<symbol id="([^"]+)"', sprite))
    used: set[str] = set()
    for script in (static / "js").rglob("*.js"):
        used |= set(re.findall(r'\bicon\("([a-z0-9-]+)"', script.read_text()))
    assert used <= known, sorted(used - known)


def test_nothing_forces_a_phone_to_scroll_sideways() -> None:
    css = (STATIC / "css" / "app.css").read_text()
    # A plain 1fr column grows with its content; a fixed minimum is wider than small phones.
    assert re.search(r"\.shell \{[^}]*var\(--nav\) minmax\(0, 1fr\)", css)
    assert "minmax(280px" not in css and "minmax(380px" not in css
    assert "overflow-x: clip" in css


def test_line_height_does_not_overwrite_the_border_colour() -> None:
    # --line is the colour of all borders; the reading setting has its own variable.
    assert '"--line"' not in (STATIC / "js" / "accessibility.js").read_text()
    assert "var(--line, " not in (STATIC / "css" / "app.css").read_text()


def test_icons_do_not_depend_on_a_request_per_use() -> None:
    for script in STATIC.rglob("*.js"):
        assert "sprite.svg#" not in script.read_text(), script.name


def test_no_window_prompt_anywhere() -> None:
    """It does not exist in the desktop app, so everything behind it silently does nothing."""
    static = Path(__file__).parents[1] / "src/pi_guard/web/static/js"
    for script in static.rglob("*.js"):
        assert not re.search(r"(?<![\w.])prompt\(", script.read_text()), script.name


def test_loading_placeholder_disappears_for_views_that_only_append() -> None:
    # People and Live add to <main>; the placeholder would stay above them.
    app = (STATIC / "js" / "app.js").read_text()
    assert "waiting.remove()" in app.split("finally", 1)[1]


def test_first_scripts_named_in_the_page_exist() -> None:
    # A wrong name costs a failed request on every start and nobody notices.
    page = (STATIC / "index.html").read_text()
    named = re.findall(r'rel="modulepreload" href="/static/([^"]+)"', page)
    assert named
    assert all((STATIC / path).is_file() for path in named)


def test_nothing_redraws_a_page_that_was_left() -> None:
    # A bare setTimeout(draw) keeps asking the Pi after the page is gone, forever on Storage.
    for script in (STATIC / "js").rglob("*.js"):
        assert "setTimeout(draw" not in script.read_text(), script.name


def test_every_step_of_a_page_change_checks_that_it_is_still_the_newest() -> None:
    # Without this, a slow page comes back over the one opened after it.
    body = (STATIC / "js" / "app.js").read_text().split("async function show(turn)", 1)[1]
    body = body.split("async function start()", 1)[0]
    for wait in ("await currentSession(name);", "await loader();"):
        for part in body.split(wait)[1:]:
            assert part.lstrip().startswith("if (turn !== showing) return;"), wait
    assert "cleanup = await" not in body
