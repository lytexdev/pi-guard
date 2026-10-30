"""Command-line interface: `pi-guard run`, diagnostics and administration."""

from __future__ import annotations

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
