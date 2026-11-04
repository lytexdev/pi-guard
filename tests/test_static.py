from __future__ import annotations

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
