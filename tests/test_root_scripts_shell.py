"""Root helpers run by /bin/sh must not use bash syntax: on the Pi, dash fails on it.

pi-guard-wg-status once got a bash `[[ =~ ]]` check and failed on every run.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
BASHISMS = re.compile(r"^\s*[^#\n]*(\[\[|=~|\$\(<|<<<|\bmapfile\b|\bsource\b)", re.M)


def _sh_scripts() -> list[Path]:
    return [p for p in sorted((ROOT / "deploy").rglob("*"))
            if p.is_file() and p.read_bytes().startswith(b"#!/bin/sh")]  # fmt: skip


def test_sh_helpers_have_no_bash_syntax() -> None:
    scripts = _sh_scripts()
    assert scripts
    for script in scripts:
        assert not BASHISMS.search(script.read_text()), script.name


@pytest.mark.skipif(shutil.which("dash") is None, reason="dash missing")
def test_sh_helpers_parse_with_dash() -> None:
    for script in _sh_scripts():
        subprocess.run(["dash", "-n", str(script)], check=True)
