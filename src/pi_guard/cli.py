"""Command-line interface: `pi-guard run`, diagnostics and administration."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from pydantic import ValidationError

from pi_guard import CREDIT, __version__
from pi_guard.config import Config, load_config

if TYPE_CHECKING:
    from pi_guard.detection.faces import FaceRecognizer

app = typer.Typer(no_args_is_help=True, add_completion=False, help="pi-guard home monitoring")
config_app = typer.Typer(no_args_is_help=True, help="Inspect configuration.")
audio_app = typer.Typer(no_args_is_help=True, help="Audio devices.")
telegram_app = typer.Typer(no_args_is_help=True, help="Telegram connection.")
signal_app = typer.Typer(no_args_is_help=True, help="Signal (signal-cli) connection.")
presence_app = typer.Typer(no_args_is_help=True, help="Wi-Fi presence detection.")
models_app = typer.Typer(no_args_is_help=True, help="Optional detection models.")
faces_app = typer.Typer(no_args_is_help=True, help="Enrolled people (face recognition).")
for sub, name in (
    (config_app, "config"),
    (audio_app, "audio"),
    (telegram_app, "telegram"),
    (signal_app, "signal"),
    (presence_app, "presence"),
    (models_app, "models"),
    (faces_app, "faces"),
):
    app.add_typer(sub, name=name)

EnvOption = Annotated[
    Path | None,
    typer.Option(
        "--env-file", help="Environment file (default: .env or /etc/pi-guard/pi-guard.env)"
    ),
]
_env_file: Path | None = None
_MARKS = {
    "ok": ("OK  ", "green"),
    "warn": ("WARN", "yellow"),
    "fail": ("FAIL", "red"),
    "skip": ("--  ", "bright_black"),
}


@app.callback()
def _main(env_file: EnvOption = None) -> None:
    global _env_file
    _env_file = env_file


def _config() -> Config:
    try:
        return load_config(_env_file)
    except ValidationError as exc:
        typer.secho("Configuration is invalid:", fg="red", err=True)
        for error in exc.errors():
            where = ".".join(str(p) for p in error["loc"]) or "settings"
            typer.echo(f"  {where}: {error['msg']}", err=True)
        raise typer.Exit(2) from None
    except (ValueError, OSError) as exc:
        typer.secho(f"Configuration is invalid: {exc}", fg="red", err=True)
        raise typer.Exit(2) from None


def _line(level: str, name: str, detail: str) -> None:
    mark, color = _MARKS[level]
    typer.secho(f"[{mark}] ", fg=color, nl=False)
    typer.echo(f"{name:<18} {detail}")


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(f"pi-guard {__version__} · {CREDIT}")


@app.command()
def run() -> None:
    """Run the service (normally started by systemd)."""
    from pi_guard.app import build_app
    from pi_guard.logs import setup_logging

    config = _config()
    s = config.settings
    setup_logging(s.log_level, s.log_format, s.log_file, s.secrets)
    application = build_app(config)

    async def main() -> None:
        task = asyncio.create_task(application.run())
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(main())


@app.command()
def doctor() -> None:
    """Check hardware, configuration and connections."""
    from pi_guard.diagnostics.doctor import Level, run_checks

    results = run_checks(_config())
    for check in results:
        _line(check.level.value, check.name, check.detail)
    if any(c.level is Level.FAIL for c in results):
        raise typer.Exit(1)


@app.command()
def health() -> None:
    """Exit 0 if the running service wrote a heartbeat recently (container health check)."""
    from pi_guard.diagnostics.doctor import heartbeat_age

    age = heartbeat_age(_config().settings.runtime_dir)
    if age is None or age > 60:
        typer.echo("unhealthy")
        raise typer.Exit(1)
    typer.echo(f"healthy (heartbeat {age:.0f}s ago)")


@app.command()
def status() -> None:
    """Show service state, mode, cameras and recent events."""
    from pi_guard.core.modes import mode_controller
    from pi_guard.diagnostics.doctor import heartbeat_age, service_state
    from pi_guard.diagnostics.system import system_snapshot
    from pi_guard.recording.segments import latest_segment
    from pi_guard.storage.db import Database

    config = _config()
    s = config.settings
    age = heartbeat_age(s.runtime_dir)
    beat = f", heartbeat {age:.0f}s ago" if age is not None else ", no heartbeat"
    typer.echo(f"service:   {service_state()}{beat}")
    db = Database(s.data_dir / "pi-guard.db")
    modes = mode_controller(db, None, config)
    state = modes.state
    until = (
        f" until {state.manual_until.astimezone(s.tz):%Y-%m-%d %H:%M}" if state.manual_until else ""
    )
    typer.echo(f"mode:      {state.effective} ({state.selection}{until})")
    snap = system_snapshot(s.recordings_path)
    typer.echo(f"storage:   {snap.disk_used_percent:.0f}% used, {snap.disk_free_gb:.1f} GB free")
    for camera in config.cameras:
        newest = latest_segment(s.recordings_path / camera.id)
        detail = "no recordings"
        if newest:
            seconds = max(0.0, time.time() - newest.path.stat().st_mtime)
            detail = f"last write {seconds:.0f}s ago ({newest.path.name})"
        typer.echo(f"camera:    {camera.id}: {detail}")
    typer.echo("events:")
    for event in db.recent_events(8):
        where = f" [{event.camera}]" if event.camera else ""
        typer.echo(f"  {event.ts.astimezone(s.tz):%Y-%m-%d %H:%M:%S}  {event.type}{where}")
    db.close()


@app.command()
def cameras() -> None:
    """List capture devices and print a config.yaml snippet."""
    from pi_guard.cameras import discovery

    devices = discovery.discover()
    if not devices:
        typer.echo("No V4L2 capture devices found. Is the camera plugged in? (ls /dev/video*)")
        raise typer.Exit(1)
    for device in devices:
        typer.secho(device.name, bold=True)
        typer.echo(f"  path:  {device.path}  ({device.node})")
        for fmt in device.formats:
            sizes = " ".join(f"{w}x{h}" for w, h in fmt.sizes[:8]) or "any"
            more = " …" if len(fmt.sizes) > 8 else ""
            typer.echo(f"  {fmt.name:<8} {sizes}{more}")
    typer.echo("\nconfig.yaml:\n\ncameras:")
    for device in devices:
        typer.echo(
            f"  - id: {device.slug}\n    name: {device.name}\n    device: {device.path}\n"
            f"    resolution: 1280x720\n    fps: 15"
        )


@app.command()
def mode(
    target: Annotated[str | None, typer.Argument(help="home, away or auto")] = None,
    duration: Annotated[str | None, typer.Option("--for", help="e.g. 2h, 30m")] = None,
) -> None:
    """Show or set the HOME/AWAY mode; the running service applies it within seconds."""
    from pi_guard.core.modes import AutoUnavailableError, Mode, mode_controller
    from pi_guard.core.timeutil import parse_duration
    from pi_guard.storage.db import Database

    config = _config()
    s = config.settings
    db = Database(s.data_dir / "pi-guard.db")
    modes = mode_controller(db, None, config)
    try:
        if target and target.lower() == "auto":
            modes.set_auto(actor="cli")
        elif target:
            delta = parse_duration(duration) if duration else None
            modes.set_manual(Mode(target.upper()), delta, actor="cli")
    except AutoUnavailableError:
        typer.secho("AUTO needs PI_GUARD_PRESENCE_ENABLED=true", fg="red", err=True)
        raise typer.Exit(1) from None
    except ValueError:
        typer.secho("use: pi-guard mode [home|away|auto] [--for 2h]", fg="red", err=True)
        raise typer.Exit(2) from None
    finally:
        state = modes.state
        db.close()
    typer.echo(f"{state.effective} ({state.selection})")


@app.command()
def logs(
    follow: Annotated[bool, typer.Option("-f", "--follow")] = False,
    lines: Annotated[int, typer.Option("-n")] = 200,
) -> None:
    """Show service logs from the systemd journal."""
    if shutil.which("journalctl") is None:
        typer.echo("journalctl not available; in Docker use: docker compose logs pi-guard")
        raise typer.Exit(1)
    args = ["journalctl", "-u", "pi-guard", "-n", str(lines), "--no-pager"]
    os.execvp("journalctl", args + (["-f"] if follow else []))  # noqa: S606


@config_app.command("check")
def config_check() -> None:
    """Validate .env and config.yaml."""
    config = _config()
    s = config.settings
    typer.echo(f"settings:  OK (language={s.language}, timezone={s.timezone})")
    found = "found" if s.config_file.exists() else "not found, using defaults"
    typer.echo(f"yaml:      {s.config_file} ({found})")
    typer.echo(f"cameras:   {len(config.cameras)} enabled")


@config_app.command("init")
def config_init(
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("config.yaml"),
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Write a starter config.yaml with the cameras and audio devices found on this machine."""
    from pi_guard.audio.devices import discover as discover_audio
    from pi_guard.cameras import discovery
    from pi_guard.config.starter import starter_config

    if output.exists() and not force:
        typer.secho(f"{output} exists; use --force to overwrite", fg="red", err=True)
        raise typer.Exit(1)
    output.write_text(starter_config(discovery.discover(), discover_audio()))
    typer.echo(f"wrote {output}")


@config_app.command("show")
def config_show() -> None:
    """Print the effective configuration with secrets hidden."""
    import json

    config = _config()
    # SecretStr fields serialise as "**********".
    typer.echo(json.dumps(config.settings.model_dump(mode="json"), indent=2))
    typer.echo(json.dumps(config.file.model_dump(mode="json"), indent=2))


@audio_app.command("devices")
def audio_devices() -> None:
    """List ALSA playback and capture devices."""
    from pi_guard.audio.devices import discover

    devices = discover()
    if not devices:
        typer.echo("No ALSA devices found.")
        raise typer.Exit(1)
    for device in devices:
        roles = [r for r, ok in (("playback", device.playback), ("capture", device.capture)) if ok]
        typer.echo(f"{device.alsa_name:<32} {'/'.join(roles):<16} {device.name}")


@telegram_app.command("test")
def telegram_test(
    send: Annotated[bool, typer.Option("--send", help="Send a test message to the admins")] = False,
) -> None:
    """Check the bot token and optionally message the admins."""
    import httpx

    from pi_guard.diagnostics.doctor import check_telegram

    config = _config()
    result = check_telegram(config)[0]
    _line(result.level.value, "telegram", result.detail)
    if result.level.value != "ok":
        raise typer.Exit(1)
    if send:
        s = config.settings
        assert s.telegram_bot_token is not None
        url = f"https://api.telegram.org/bot{s.telegram_bot_token.get_secret_value()}/sendMessage"
        for admin in s.telegram_admin_user_ids:
            response = httpx.post(
                url, json={"chat_id": admin, "text": "pi-guard test message"}, timeout=10
            )
            ok = response.status_code == 200
            hint = "delivered" if ok else "failed: open the bot in Telegram and press Start first"
            _line("ok" if ok else "fail", f"admin {admin}", hint)


@signal_app.command("test")
def signal_test() -> None:
    """Check the connection to the signal-cli daemon."""
    from pi_guard.diagnostics.doctor import check_signal

    result = check_signal(_config())[0]
    _line(result.level.value, "signal", result.detail)
    if result.level.value == "fail":
        raise typer.Exit(1)


@presence_app.command("status")
def presence_status() -> None:
    """Run one presence check now and show what each source reports."""
    from pi_guard.presence.monitor import build_sources

    config = _config()
    presence = config.file.presence
    devices = [d for d in presence.devices if d.enabled]
    if not devices:
        typer.echo("No trusted devices configured (presence.devices in config.yaml).")
        raise typer.Exit(1)

    async def check() -> None:
        for source in build_sources(config.settings, presence):
            results = await source.observe(devices)
            for device in devices:
                typer.echo(f"{source.name:<11} {device.name:<20} {results[device.id]}")

    asyncio.run(check())
    if not config.settings.presence_enabled:
        typer.echo("\nNote: PI_GUARD_PRESENCE_ENABLED is false; the service does not use this.")


@models_app.command("list")
def models_list() -> None:
    """Show optional models and whether they are installed."""
    from pi_guard.detection.models import MODELS

    models_dir = _config().settings.data_dir / "models"
    for model in MODELS:
        state = "installed" if model.present(models_dir) else "not installed"
        typer.echo(f"{model.name:<6} {model.size_mb:>5.1f} MB  {state:<14} {model.purpose}")


@models_app.command("download")
def models_download(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask")] = False,
) -> None:
    """Download the face models from the OpenCV Model Zoo (asks first)."""
    from pi_guard.detection.models import MODELS, ChecksumError, download

    models_dir = _config().settings.data_dir / "models"
    missing = [m for m in MODELS if not m.present(models_dir)]
    if not missing:
        typer.echo("All models are installed.")
        return
    typer.echo("The following files will be downloaded:")
    for model in missing:
        typer.echo(f"  {model.filename} ({model.size_mb} MB, {model.license}) – {model.purpose}")
        typer.echo(f"    from {model.url}")
    if not yes and not typer.confirm("Download now?"):
        raise typer.Exit(1)
    for model in missing:
        try:
            path = download(model, models_dir)
        except (ChecksumError, OSError) as exc:
            typer.secho(f"{model.filename}: {exc}", fg="red", err=True)
            raise typer.Exit(1) from None
        typer.echo(f"saved {path}")


def _recognizer(config: Config) -> FaceRecognizer:
    from pi_guard.detection.faces import BiometricStore, FaceRecognizer
    from pi_guard.detection.persons import PersonDetector

    s = config.settings
    models_dir = s.data_dir / "models"
    try:
        return FaceRecognizer(
            config.file.faces,
            models_dir,
            BiometricStore(s.data_dir / "biometrics.db"),
            PersonDetector(models_dir),
        )
    except (FileNotFoundError, ImportError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from None


@faces_app.command("list")
def faces_list() -> None:
    """List enrolled people."""
    people = _recognizer(_config()).store.people()
    for name, samples, enrolled in people:
        typer.echo(f"{name:<24} {samples} sample(s), enrolled {enrolled[:10]}")
    if not people:
        typer.echo("Nobody is enrolled.")


@faces_app.command("enroll")
def faces_enroll(name: str, files: list[Path]) -> None:
    """Enroll a person from photos or videos that show only that person."""
    from pi_guard.detection.enrollment import EnrollmentSession, video_frames
    from pi_guard.detection.faces import EnrollmentError

    typer.echo("Only enroll people who have agreed to be recognised. Files are not stored.")
    if not typer.confirm(f"Has {name} given consent?"):
        raise typer.Exit(1)
    recognizer = _recognizer(_config())
    session = EnrollmentSession(name=name)
    limit = recognizer.settings.max_samples_per_person
    for file in files:
        images = [file]
        workdir = None
        if file.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm", ".avi"):
            workdir, images = asyncio.run(video_frames(file))
        added = 0
        for image in images:
            try:
                added += session.add(recognizer.prepare(image), limit)
            except EnrollmentError as exc:
                if workdir is None:
                    typer.secho(f"{file}: {exc.key}", fg="red", err=True)
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)
        typer.echo(f"{file}: {added} sample(s)")
    count = 0
    for embedding in session.samples:
        count = recognizer.enroll(name, embedding, "cli")
    typer.echo(f"{name}: {count} sample(s) stored")


@faces_app.command("delete")
def faces_delete(name: str) -> None:
    """Delete a person and all their face data."""
    if not _recognizer(_config()).delete(name):
        typer.echo(f"{name} is not enrolled.")
        raise typer.Exit(1)
    typer.echo(f"{name} deleted.")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)
