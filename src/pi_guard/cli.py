"""Command-line interface: `pi-guard run`, diagnostics and administration."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from pi_guard import __version__
from pi_guard.config import Config, load_config

app = typer.Typer(no_args_is_help=True, add_completion=False, help="pi-guard home monitoring")
config_app = typer.Typer(no_args_is_help=True, help="Inspect configuration.")
app.add_typer(config_app, name="config")

EnvOption = Annotated[
    Path | None,
    typer.Option(
        "--env-file", help="Environment file (default: .env or /etc/pi-guard/pi-guard.env)"
    ),
]
_env_file: Path | None = None


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
    except ValueError as exc:
        typer.secho(f"Configuration is invalid: {exc}", fg="red", err=True)
        raise typer.Exit(2) from None


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command()
def run() -> None:
    """Run the service (normally started by systemd)."""
    import asyncio
    import signal

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


@config_app.command("check")
def config_check() -> None:
    """Validate .env and config.yaml."""
    config = _config()
    s = config.settings
    typer.echo(f"settings:  OK (language={s.language}, timezone={s.timezone})")
    found = "found" if s.config_file.exists() else "not found, using defaults"
    typer.echo(f"yaml:      {s.config_file} ({found})")
    typer.echo(f"cameras:   {len(config.cameras)} enabled")


def main() -> None:
    app()
