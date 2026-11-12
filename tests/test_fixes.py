"""Fixes from the security check: a fixed list, two confirmations, and always a way back."""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

from pi_guard.diagnostics import fixes
from pi_guard.diagnostics.scan import check, rootkit_findings

from .test_web import PASSWORD, _client, _login

ROOT = Path(__file__).resolve().parents[1]


def _maint():  # type: ignore[no-untyped-def]
    path = str(ROOT / "deploy/maint/pi-guard-maint")
    loader = importlib.machinery.SourceFileLoader("pi_guard_maint", path)
    spec = importlib.util.spec_from_loader("pi_guard_maint", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_every_fix_has_a_word_the_root_job_takes() -> None:
    maint = _maint()
    for name in fixes.FIXES:
        assert name in maint.FIXES
        for word in (f"fix-{name}", f"undo-{name}"):
            assert word in maint.JOBS and len(word) <= 32  # take_request reads 32 characters


def test_fixes_fit_the_result() -> None:
    last = {"checks": [check("ssh", "bad", "permitrootlogin=yes, passwordauthentication=yes"),
                       check("auto_updates", "warn"), check("firewall", "ok"),
                       check("rootkits", "bad", "rkhunter; Warning: x")]}  # fmt: skip
    assert fixes.offered(last) == ["ssh-root", "ssh-passwords", "auto-updates", "rkhunter"]
    assert fixes.offered(None) == []


def test_known_harmless_rkhunter_lines_are_not_findings() -> None:
    lines = ["Warning: Changes found in the group file for group 'gpio':",
             "User 'pi-guard' has been added to the group",
             "Warning: Suspicious file types found in /dev:", "/dev/shm/FTL-1227-lock: data",
             "Warning: The file properties have changed: /usr/bin/curl"]  # fmt: skip
    kept, known = rootkit_findings(lines)
    assert kept == ["Warning: The file properties have changed: /usr/bin/curl"] and known == 4


def test_the_review_can_only_name_listed_fixes() -> None:
    answer = (
        'Sure: {"summary": "Two things.", "steps": [{"title": "Root", "why": "risky", '
        '"fix": "ssh-root", "commands": []}, {"title": "Wipe", "why": "x", '
        '"fix": "rm -rf /", "commands": ["sudo apt upgrade", 5]}]}'
    )
    review = fixes.parse_review(answer)
    assert review["summary"] == "Two things."
    assert [s["fix"] for s in review["steps"]] == ["ssh-root", None]
    assert review["steps"][1]["commands"] == ["sudo apt upgrade"]
    assert fixes.parse_review("no json at all")["steps"] == []


async def test_a_fix_needs_the_password_and_its_name(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pi_guard.web import api_tools

    monkeypatch.setattr(api_tools, "HELPER", tmp_path / "pi-guard-maint")
    (tmp_path / "pi-guard-maint").write_text("")
    client, app = await _client(tmp_path)
    try:
        h = {"X-CSRF-Token": await _login(client)}
        url = "/api/scan/fix"
        assert (await client.post(url, json={"id": "rm-rf"}, headers=h)).status == 404
        wrong = {"id": "ssh-root", "password": "nope", "confirm": "ssh-root"}
        assert (await client.post(url, json=wrong, headers=h)).status == 403
        (app.settings.data_dir / "maint").mkdir(parents=True, exist_ok=True)
        untyped = {"id": "ssh-root", "password": PASSWORD, "confirm": "yes"}
        assert (await client.post(url, json=untyped, headers=h)).status == 400
        request = app.settings.data_dir / "maint" / "request"
        request.parent.mkdir(parents=True, exist_ok=True)  # install.sh makes it
        assert not request.exists()
        right = {"id": "ssh-root", "password": PASSWORD, "confirm": " SSH-root "}
        assert (await client.post(url, json=right, headers=h)).status == 200
        assert request.read_text() == "fix-ssh-root"
        undo = {"id": "ssh-root", "password": PASSWORD, "undo": True}
        assert (await client.post(url, json=undo, headers=h)).status == 200
        assert request.read_text() == "undo-ssh-root"
    finally:
        await client.close()
        app.db.close()
