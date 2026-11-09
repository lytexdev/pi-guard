"""Deployment settings read from the environment and the `.env` file."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from pi_guard.core.modes import Mode

_TOKEN_RE = re.compile(r"^(\d{5,15}):[A-Za-z0-9_-]{30,}$")
_PHONE_RE = re.compile(r"^\+[1-9]\d{6,14}$")

IntList = Annotated[list[int], NoDecode]
StrList = Annotated[list[str], NoDecode]
MapProvider = Literal["openstreetmap", "google", "apple", "bing", "here", "geo"]


def _split(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PI_GUARD_",
        env_file=None,
        extra="ignore",
        case_sensitive=False,
    )

    language: str = Field("en", pattern=r"^[a-z]{2}(-[a-z]{2})?$")
    timezone: str = "Europe/Berlin"
    # Which map a phone's place opens in, see MAP_PROVIDERS in addons/mobile.py.
    map_provider: MapProvider = "openstreetmap"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["text", "json"] = "text"
    log_file: Path | None = None

    data_dir: Path = Path("/var/lib/pi-guard")
    recordings_dir: Path | None = None
    config_file: Path = Path("/etc/pi-guard/config.yaml")
    runtime_dir: Path = Path("/run/pi-guard")

    telegram_enabled: bool = True
    telegram_bot_token: SecretStr | None = None
    telegram_bot_id: int | None = None
    telegram_admin_user_ids: IntList = Field(default_factory=list)
    telegram_viewer_user_ids: IntList = Field(default_factory=list)
    telegram_allowed_chat_ids: IntList = Field(default_factory=list)
    telegram_group_plain_text: bool = False

    # Discord (optional bot in direct messages; see docs/discord.md)
    discord_enabled: bool = False
    discord_bot_token: SecretStr | None = None
    discord_admin_user_ids: StrList = Field(default_factory=list)
    discord_viewer_user_ids: StrList = Field(default_factory=list)

    # Phones with the pi-guard app (see docs/mobile.md)
    mobile_enabled: bool = True

    # Email (optional, alerts and exports only; see docs/email.md)
    email_enabled: bool = False
    email_host: str = ""
    email_port: int = Field(587, ge=1, le=65535)
    email_security: Literal["starttls", "tls"] = "starttls"
    email_username: str = ""
    email_password: SecretStr | None = None
    email_from: str = ""
    email_recipients: StrList = Field(default_factory=list)
    email_export: Literal["off", "weekly", "monthly"] = "off"

    signal_enabled: bool = False
    signal_phone_number: str | None = None
    signal_admin_numbers: StrList = Field(default_factory=list)
    signal_allowed_numbers: StrList = Field(default_factory=list)
    signal_cli_address: str = "127.0.0.1:7583"

    recording_enabled: bool = True
    recording_continuous: bool = True
    recording_retention_days: int = Field(7, ge=1, le=365)
    # Keep everything this long, then only what is around events. 0: everything until retention.
    recording_full_hours: int = Field(0, ge=0, le=720)
    recording_max_storage_percent: int = Field(85, ge=30, le=98)
    recording_min_free_mb: int = Field(1024, ge=100)
    recording_segment_seconds: int = Field(300, ge=30, le=3600)
    recording_buffer_minutes: int = Field(15, ge=2, le=240)

    motion_enabled: bool = True
    motion_sensitivity: int = Field(50, ge=0, le=100)

    default_mode: Mode = Mode.HOME
    presence_enabled: bool = False
    # Which way presence detection may switch the mode on its own.
    presence_auto_home: bool = True
    presence_auto_away: bool = True
    person_detection_enabled: bool = False
    face_recognition_enabled: bool = False

    audio_recording_enabled: bool = False
    audio_playback_enabled: bool = False
    audio_max_volume: int = Field(80, ge=0, le=100)

    mqtt_enabled: bool = False
    mqtt_host: str | None = None
    mqtt_port: int = Field(1883, ge=1, le=65535)
    mqtt_username: str | None = None
    mqtt_password: SecretStr | None = None
    mqtt_tls: bool = False
    mqtt_topic_prefix: str = "pi-guard"
    mqtt_home_assistant_discovery: bool = False
    mqtt_commands_enabled: bool = False

    fritzbox_username: str = ""  # empty: the file setting, or the user the box names
    fritzbox_password: SecretStr | None = None

    # Emergency contacts: written to when alerts stay unacknowledged.
    emergency_telegram_user_ids: IntList = Field(default_factory=list)
    emergency_signal_numbers: StrList = Field(default_factory=list)
    emergency_discord_user_ids: StrList = Field(default_factory=list)
    emergency_after_alerts: int = Field(3, ge=1, le=50)
    emergency_after_minutes: int = Field(30, ge=1, le=1440)

    # Network integrations (optional)
    pihole_enabled: bool = False
    pihole_url: str = "http://127.0.0.1:8080"
    pihole_password: SecretStr | None = None
    wireguard_enabled: bool = False
    wireguard_interface: str = "wg0"
    # Where phones reach home from outside: MyFRITZ!/DynDNS name or public IP, optional :port.
    wireguard_endpoint: str | None = None
    # A name with an IPv6 address of this device itself, for connections without IPv4.
    wireguard_endpoint_v6: str | None = None
    # A rented server that passes the VPN on; written by scripts/relay.sh.
    wireguard_relay: str | None = None
    # The home network, reachable for VPN devices set to "home network only".
    wireguard_home_network: str | None = None

    # Alerts into other services (optional, send-only; see docs/addons.md)
    chatapps_enabled: bool = False
    slack_webhook_url: SecretStr | None = None
    rocketchat_webhook_url: SecretStr | None = None
    matrix_homeserver: str | None = None
    matrix_room: str | None = None
    matrix_token: SecretStr | None = None
    mastodon_instance: str | None = None
    mastodon_token: SecretStr | None = None

    # WhatsApp (optional, official Cloud API; see docs/whatsapp.md)
    whatsapp_enabled: bool = False
    whatsapp_phone_number_id: str | None = None
    whatsapp_access_token: SecretStr | None = None
    whatsapp_app_secret: SecretStr | None = None
    whatsapp_verify_token: SecretStr | None = None
    whatsapp_admin_numbers: StrList = Field(default_factory=list)
    whatsapp_allowed_numbers: StrList = Field(default_factory=list)
    # Approved template for alerts outside WhatsApp's 24-hour reply window.
    whatsapp_alert_template: str | None = None

    # AI assistant add-on: keys stay here; everything else is in config.yaml (addons.assistant)
    assistant_api_key: SecretStr | None = None
    assistant_deepl_key: SecretStr | None = None
    assistant_paperless_token: SecretStr | None = None
    assistant_nextcloud_password: SecretStr | None = None

    # Second address of the Pi for the block page, see scripts/blockpage.sh.
    blockpage_ip: str | None = None

    # Web dashboard (optional)
    dashboard_enabled: bool = True
    dashboard_host: str = "0.0.0.0"  # noqa: S104 - the dashboard is meant for the LAN
    dashboard_port: int = Field(8443, ge=1, le=65535)
    dashboard_tls: bool = True
    dashboard_tls_cert: Path | None = None
    dashboard_tls_key: Path | None = None
    # Extra names/addresses for the certificate, e.g. the host's LAN IP in Docker.
    dashboard_tls_names: StrList = Field(default_factory=list)
    # Comma-separated: password, keyfile; or "none" (only with ALLOW_UNAUTHENTICATED).
    dashboard_auth: StrList = Field(default_factory=lambda: ["password"])
    # Pepper for the dashboard password hash and key for the stored TOTP secret.
    # Generated in DATA_DIR if empty; keeping it in the env file separates it from the database.
    dashboard_secret_key: SecretStr | None = None
    # The first login's password, chosen by install.sh. Without it, the well-known admin123.
    dashboard_first_password: SecretStr | None = None
    # How the dashboard is reached from outside, e.g. https://guard.example.org behind a
    # reverse proxy. Only this name and addresses of the home network are accepted then.
    dashboard_public_url: str | None = None
    # Fetch signed releases of the agent, desktop and phone app from GitHub (core/app_updates.py).
    app_updates_from_github: bool = True
    # Where anonymous statistics go when the owner switches them on (diagnostics/contribution.py).
    support_url: str | None = None
    dashboard_allow_unauthenticated: bool = False
    dashboard_rate_limit_per_minute: int = Field(240, ge=30, le=10000)
    dashboard_honeypot: bool = True
    dashboard_ban_minutes: int = Field(60, ge=1, le=10080)
    dashboard_trusted_proxies: StrList = Field(default_factory=list)
    # Live view: frames per second (0 turns it off) and width of the live picture.
    dashboard_live_fps: float = Field(8, ge=0, le=30)
    dashboard_live_width: int = Field(960, ge=320, le=1920)
    # Push notifications to installed dashboard apps; payloads are end-to-end
    # encrypted but travel through the browser vendor's push service.
    webpush_enabled: bool = False
    # Contact the push services may use to reach the operator (VAPID "sub").
    webpush_contact: str = "mailto:pi-guard@localhost"

    @field_validator(
        "telegram_admin_user_ids",
        "telegram_viewer_user_ids",
        "telegram_allowed_chat_ids",
        "signal_admin_numbers",
        "whatsapp_admin_numbers",
        "whatsapp_allowed_numbers",
        "signal_allowed_numbers",
        "dashboard_auth",
        "dashboard_trusted_proxies",
        "emergency_telegram_user_ids",
        "emergency_signal_numbers",
        "emergency_discord_user_ids",
        "discord_admin_user_ids",
        "discord_viewer_user_ids",
        "email_recipients",
        "dashboard_tls_names",
        mode="before",
    )
    @classmethod
    def _parse_list(cls, value: object) -> object:
        return _split(value)

    @field_validator("default_mode", mode="before")
    @classmethod
    def _upper_mode(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value

    @field_validator(
        "discord_admin_user_ids", "discord_viewer_user_ids", "emergency_discord_user_ids"
    )
    @classmethod
    def _check_discord_ids(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.isdigit():
                raise ValueError("Discord user IDs are numbers; copy them with developer mode")
        return values

    @field_validator("email_recipients")
    @classmethod
    def _check_recipients(cls, values: list[str]) -> list[str]:
        for value in values:
            if "@" not in value or any(c in value for c in " ,;<>\r\n"):
                raise ValueError("recipients must be plain email addresses")
        return values

    @field_validator("signal_phone_number")
    @classmethod
    def _check_phone(cls, value: str | None) -> str | None:
        if value and not _PHONE_RE.match(value):
            raise ValueError("phone numbers must use international format, e.g. +4915112345678")
        return value or None

    @field_validator("signal_admin_numbers", "signal_allowed_numbers", "emergency_signal_numbers")
    @classmethod
    def _check_phones(cls, values: list[str]) -> list[str]:
        for value in values:
            if not _PHONE_RE.match(value):
                raise ValueError(f"{value!r} is not in international format (+49...)")
        return values

    @field_validator("signal_cli_address")
    @classmethod
    def _check_address(cls, value: str) -> str:
        host, sep, port = value.rpartition(":")
        if not sep or not host or not port.isdigit():
            raise ValueError("expected host:port")
        return value

    @field_validator("wireguard_interface")
    @classmethod
    def _check_interface(cls, value: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_.-]{1,15}$", value):
            raise ValueError("not a network interface name")
        return value

    @field_validator("dashboard_public_url")
    @classmethod
    def _check_public_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip().rstrip("/")
        if not re.match(r"^https://[A-Za-z0-9.-]{1,253}(:\d{1,5})?$", value):
            raise ValueError("expected a URL like https://guard.example.org")
        return value

    @field_validator("support_url")
    @classmethod
    def _check_support_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not re.match(r"^https://[A-Za-z0-9.-]{1,253}(:\d{1,5})?(/[A-Za-z0-9._~/-]*)?$", value):
            raise ValueError("expected a URL like https://stats.example.org/v1/report")
        return value

    @field_validator("wireguard_endpoint", "wireguard_endpoint_v6", "wireguard_relay")
    @classmethod
    def _check_endpoint(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not re.match(
            r"^(\[[0-9a-fA-F:]+\]|[A-Za-z0-9.-]{1,253}|[0-9a-fA-F:]+)(:\d{1,5})?$", value
        ):
            raise ValueError("expected a host name or IP address, optionally with :port")
        return value

    @field_validator("wireguard_home_network")
    @classmethod
    def _check_home_network(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        try:
            network = ipaddress.ip_network(value.strip(), strict=False)
        except ValueError:
            raise ValueError("expected a network like 192.168.178.0/24") from None
        if not network.is_private:
            raise ValueError("the home network must be a private range")
        return str(network)

    @field_validator("webpush_contact")
    @classmethod
    def _check_contact(cls, value: str) -> str:
        if not value.startswith(("mailto:", "https://")):
            raise ValueError("expected mailto:… or https://…")
        return value

    @field_validator("pihole_url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("expected http://… or https://…")
        return value.rstrip("/")

    @field_validator("mqtt_topic_prefix")
    @classmethod
    def _check_prefix(cls, value: str) -> str:
        value = value.strip("/")
        if not value or any(c in value for c in "#+"):
            raise ValueError("topic prefix must be non-empty and must not contain wildcards")
        return value

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        token = self.telegram_bot_token.get_secret_value() if self.telegram_bot_token else ""
        # With the dashboard, Telegram can be set up later in its setup assistant;
        # until then it simply stays off.
        if self.telegram_enabled and not (self.dashboard_enabled and not token):
            match = _TOKEN_RE.match(token)
            if not match:
                raise ValueError(
                    "PI_GUARD_TELEGRAM_BOT_TOKEN is missing or malformed "
                    "(expected the token from @BotFather, e.g. 123456789:AA...)"
                )
            derived = int(match.group(1))
            if self.telegram_bot_id is not None and self.telegram_bot_id != derived:
                raise ValueError(
                    "PI_GUARD_TELEGRAM_BOT_ID does not match the bot token; leave it empty"
                )
            self.telegram_bot_id = derived
            if not self.telegram_admin_user_ids and not self.dashboard_enabled:
                raise ValueError("PI_GUARD_TELEGRAM_ADMIN_USER_IDS needs at least one user ID")
            if derived in self.telegram_admin_user_ids:
                raise ValueError(
                    "the bot's own ID was listed as an admin; use your personal Telegram user ID"
                )
        if self.discord_enabled:
            bot_token = self.discord_bot_token
            if not (bot_token and bot_token.get_secret_value()):
                raise ValueError("PI_GUARD_DISCORD_BOT_TOKEN is required when Discord is enabled")
            if not self.discord_admin_user_ids:
                raise ValueError("PI_GUARD_DISCORD_ADMIN_USER_IDS needs at least one user ID")
        if self.email_enabled and not (
            self.email_host and "@" in self.email_from and self.email_recipients
        ):
            raise ValueError("email needs a server, a sender address and at least one recipient")
        if self.signal_enabled:
            if not self.signal_phone_number:
                raise ValueError("PI_GUARD_SIGNAL_PHONE_NUMBER is required when Signal is enabled")
            if not self.signal_admin_numbers:
                raise ValueError("PI_GUARD_SIGNAL_ADMIN_NUMBERS needs at least one number")
        if self.whatsapp_enabled:
            access = self.whatsapp_access_token
            if not self.whatsapp_phone_number_id or not (access and access.get_secret_value()):
                raise ValueError(
                    "WhatsApp needs PI_GUARD_WHATSAPP_PHONE_NUMBER_ID and _ACCESS_TOKEN"
                )
            if not self.whatsapp_admin_numbers:
                raise ValueError("PI_GUARD_WHATSAPP_ADMIN_NUMBERS needs at least one number")
        if self.dashboard_enabled:
            self._check_dashboard()
        if self.webpush_enabled and not self.dashboard_enabled:
            raise ValueError("push notifications need the dashboard (PI_GUARD_DASHBOARD_ENABLED)")
        if self.mqtt_enabled and not self.mqtt_host:
            raise ValueError("PI_GUARD_MQTT_HOST is required when MQTT is enabled")
        if self.face_recognition_enabled and not self.person_detection_enabled:
            raise ValueError(
                "face recognition builds on person detection; "
                "enable PI_GUARD_PERSON_DETECTION_ENABLED as well"
            )
        return self

    def _check_dashboard(self) -> None:
        methods = {m.lower() for m in self.dashboard_auth}
        if not methods or not methods <= {"password", "keyfile", "telegram", "webauthn", "none"}:
            raise ValueError(
                "PI_GUARD_DASHBOARD_AUTH: use password, keyfile, telegram, webauthn or none"
            )
        if "none" in methods:
            if len(methods) > 1:
                raise ValueError("PI_GUARD_DASHBOARD_AUTH=none cannot be combined")
            loopback = self.dashboard_host in ("127.0.0.1", "::1", "localhost")
            if not loopback and not self.dashboard_allow_unauthenticated:
                raise ValueError(
                    "a dashboard without login on the network needs "
                    "PI_GUARD_DASHBOARD_ALLOW_UNAUTHENTICATED=true"
                )
        key = self.dashboard_secret_key.get_secret_value() if self.dashboard_secret_key else ""
        if key and len(key) < 32:
            raise ValueError(
                "PI_GUARD_DASHBOARD_SECRET_KEY needs at least 32 characters "
                "(e.g. openssl rand -hex 32)"
            )

    @property
    def telegram_ready(self) -> bool:
        return (
            self.telegram_enabled
            and self.telegram_bot_token is not None
            and bool(self.telegram_bot_token.get_secret_value())
        )

    @property
    def dashboard_methods(self) -> set[str]:
        return {m.lower() for m in self.dashboard_auth}

    @property
    def recordings_path(self) -> Path:
        return self.recordings_dir or self.data_dir / "recordings"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def secrets(self) -> list[str]:
        values = [
            self.telegram_bot_token,
            self.mqtt_password,
            self.fritzbox_password,
            self.dashboard_secret_key,
            self.pihole_password,
        ]
        return [v.get_secret_value() for v in values if v and v.get_secret_value()]
