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
