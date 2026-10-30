"""Static checks of the deployment files (no Docker or systemd needed)."""

from __future__ import annotations

from pathlib import Path

import yaml

from pi_guard.config.files import load_file_config
from pi_guard.config.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_compose_file_is_safe_by_default() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    service = compose["services"]["pi-guard"]
    assert not service.get("privileged")
    assert service.get("network_mode") != "host"
    assert service["cap_drop"] == ["ALL"]
    assert service["read_only"] is True
    volumes = " ".join(service["volumes"])
    assert "docker.sock" not in volumes
    assert {"/config", "/data", "/recordings"} <= {v.split(":")[1] for v in service["volumes"]}


def test_dockerfile_runs_unprivileged_with_healthcheck() -> None:
    text = (ROOT / "Dockerfile").read_text()
    assert "USER pi-guard" in text
    assert "HEALTHCHECK" in text and "pi-guard" in text


def test_systemd_unit_does_not_run_as_root() -> None:
    unit = (ROOT / "deploy/systemd/pi-guard.service").read_text()
    assert "User=pi-guard" in unit
    assert "NoNewPrivileges=yes" in unit
    assert "ProtectSystem=strict" in unit


def test_examples_are_valid() -> None:
    settings = Settings(_env_file=ROOT / ".env.example", telegram_enabled=False)  # type: ignore[call-arg]
    assert settings.language == "de"
    config = load_file_config(ROOT / "config/config.example.yaml")
    assert config.cameras and config.presence.devices


def test_env_example_has_no_trailing_comments() -> None:
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line and not line.startswith("#"):
            assert "#" not in line, line


def test_no_ci_workflows() -> None:
    assert not (ROOT / ".github/workflows").exists()
