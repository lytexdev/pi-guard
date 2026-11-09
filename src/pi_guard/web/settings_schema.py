"""Which `.env` settings the dashboard may change, and how to present them.

Secrets (tokens, passwords) are deliberately not editable here; the dashboard
only shows whether they are set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, get_args
from zoneinfo import available_timezones

from pi_guard.config.settings import MapProvider
from pi_guard.i18n import LANGUAGES


@dataclass(frozen=True)
class Field:
    key: str  # lower-case settings attribute
    group: str
    kind: str  # bool | int | choice | multi | text | list
    minimum: int | None = None
    maximum: int | None = None
    options: tuple[str, ...] = field(default_factory=tuple)


LOGIN_METHODS = ("password", "keyfile", "telegram", "code", "webauthn", "none")
TIMEZONES = tuple(sorted(z for z in available_timezones() if "/" in z or z == "UTC"))

FIELDS = (
    Field("language", "general", "language"),
    Field("timezone", "general", "choice", options=TIMEZONES),
    Field("map_provider", "general", "choice", options=get_args(MapProvider)),
    Field("log_level", "general", "choice", options=("DEBUG", "INFO", "WARNING", "ERROR")),
    Field("recording_enabled", "recording", "bool"),
    Field("recording_continuous", "recording", "bool"),
    Field("recording_retention_days", "recording", "int", 1, 365),
    Field("recording_full_hours", "recording", "int", 0, 720),
    Field("recording_max_storage_percent", "recording", "int", 30, 98),
    Field("recording_segment_seconds", "recording", "int", 30, 3600),
    Field("recording_buffer_minutes", "recording", "int", 2, 240),
    Field("motion_enabled", "detection", "bool"),
    Field("motion_sensitivity", "detection", "int", 0, 100),
    Field("person_detection_enabled", "detection", "bool"),
    Field("face_recognition_enabled", "detection", "bool"),
    Field("default_mode", "modes", "choice", options=("HOME", "AWAY")),
    Field("presence_enabled", "modes", "bool"),
    Field("presence_auto_home", "modes", "bool"),
    Field("presence_auto_away", "modes", "bool"),
    Field("audio_recording_enabled", "audio", "bool"),
    Field("audio_playback_enabled", "audio", "bool"),
    Field("audio_max_volume", "audio", "int", 0, 100),
    Field("telegram_enabled", "messaging", "bool"),
    # Write-only: the dashboard never shows it again; empty keeps the current token.
    Field("telegram_bot_token", "messaging", "secret"),
    Field("telegram_admin_user_ids", "messaging", "list"),
    Field("telegram_viewer_user_ids", "messaging", "list"),
    Field("telegram_allowed_chat_ids", "messaging", "list"),
    Field("discord_enabled", "discord", "bool"),
    Field("discord_bot_token", "discord", "secret"),
    Field("discord_admin_user_ids", "discord", "list"),
    Field("discord_viewer_user_ids", "discord", "list"),
    Field("mobile_enabled", "mobile", "bool"),
    Field("email_enabled", "email", "bool"),
    Field("email_host", "email", "text"),
    Field("email_port", "email", "int", 1, 65535),
    Field("email_security", "email", "choice", options=("starttls", "tls")),
    Field("email_username", "email", "text"),
    Field("email_password", "email", "secret"),
    Field("email_from", "email", "text"),
    Field("email_recipients", "email", "list"),
    Field("email_export", "email", "choice", options=("off", "weekly", "monthly")),
    Field("chatapps_enabled", "chatapps", "bool"),
    Field("slack_webhook_url", "chatapps", "secret"),
    Field("rocketchat_webhook_url", "chatapps", "secret"),
    Field("matrix_homeserver", "chatapps", "text"),
    Field("matrix_room", "chatapps", "text"),
    Field("matrix_token", "chatapps", "secret"),
    Field("mastodon_instance", "chatapps", "text"),
    Field("mastodon_token", "chatapps", "secret"),
    Field("signal_enabled", "signal", "bool"),
    Field("signal_commands", "signal", "bool"),
    Field("signal_phone_number", "signal", "text"),
    Field("signal_admin_numbers", "signal", "list"),
    Field("signal_allowed_numbers", "signal", "list"),
    Field("signal_cli_address", "signal", "text"),
    Field("emergency_telegram_user_ids", "emergency", "list"),
    Field("emergency_signal_numbers", "emergency", "list"),
    Field("emergency_discord_user_ids", "emergency", "list"),
    Field("emergency_after_alerts", "emergency", "int", 1, 50),
    Field("emergency_after_minutes", "emergency", "int", 1, 1440),
    Field("mqtt_enabled", "integrations", "bool"),
    Field("mqtt_host", "integrations", "text"),
    Field("mqtt_port", "integrations", "int", 1, 65535),
    Field("pihole_enabled", "network", "bool"),
    Field("pihole_url", "network", "text"),
    Field("fritzbox_username", "network", "text"),
    Field("fritzbox_password", "network", "secret"),
    Field("wireguard_enabled", "network", "bool"),
    Field("wireguard_endpoint", "network", "text"),
    Field("wireguard_endpoint_v6", "network", "text"),
    Field("assistant_api_key", "assistant", "secret"),
    Field("assistant_deepl_key", "assistant", "secret"),
    Field("assistant_paperless_token", "assistant", "secret"),
    Field("assistant_nextcloud_password", "assistant", "secret"),
    Field("whatsapp_enabled", "whatsapp", "bool"),
    Field("whatsapp_phone_number_id", "whatsapp", "text"),
    Field("whatsapp_access_token", "whatsapp", "secret"),
    Field("whatsapp_app_secret", "whatsapp", "secret"),
    Field("whatsapp_verify_token", "whatsapp", "secret"),
    Field("whatsapp_admin_numbers", "whatsapp", "list"),
    Field("whatsapp_allowed_numbers", "whatsapp", "list"),
    Field("whatsapp_alert_template", "whatsapp", "text"),
    Field(
        "dashboard_auth",
        "dashboard",
        "multi",
        options=LOGIN_METHODS,
    ),
    Field("dashboard_public_url", "dashboard", "text"),
    Field("support_url", "dashboard", "text"),
    Field("app_updates_from_github", "dashboard", "bool"),
    Field("backup_enabled", "backup", "bool"),
    Field("backup_target", "backup", "choice", options=("nextcloud", "rsync", "borg")),
    Field("backup_schedule", "backup", "choice", options=("daily", "weekly")),
    Field("backup_hour", "backup", "int", 0, 23),
    Field("backup_keep", "backup", "int", 1, 90),
    Field("backup_recordings", "backup", "bool"),
    Field("backup_passphrase", "backup", "secret"),
    Field("backup_nextcloud_url", "backup", "text"),
    Field("backup_nextcloud_user", "backup", "text"),
    Field("backup_nextcloud_password", "backup", "secret"),
    Field("backup_nextcloud_folder", "backup", "text"),
    Field("backup_rsync_target", "backup", "text"),
    Field("backup_borg_repo", "backup", "text"),
    Field("dashboard_trusted_proxies", "dashboard", "list"),
    Field("webpush_enabled", "dashboard", "bool"),
    Field("dashboard_honeypot", "dashboard", "bool"),
    Field("dashboard_ban_minutes", "dashboard", "int", 1, 10080),
    Field("dashboard_rate_limit_per_minute", "dashboard", "int", 30, 10000),
)
BY_KEY = {f.key: f for f in FIELDS}
# Only shown in expert mode; everything else is what most people ever need.
EXPERT = frozenset({
    "log_level", "recording_max_storage_percent", "recording_segment_seconds",
    "recording_buffer_minutes", "telegram_viewer_user_ids", "telegram_allowed_chat_ids",
    "discord_viewer_user_ids", "email_port", "email_security", "email_from",
    "signal_admin_numbers", "signal_allowed_numbers", "signal_cli_address",
    "emergency_after_alerts", "emergency_after_minutes", "whatsapp_phone_number_id",
    "whatsapp_app_secret", "whatsapp_verify_token", "whatsapp_alert_template",
    "wireguard_endpoint_v6", "dashboard_auth", "dashboard_public_url",
    "dashboard_trusted_proxies", "support_url", "app_updates_from_github", "webpush_enabled",
    "dashboard_honeypot",
    "dashboard_ban_minutes",
    "dashboard_rate_limit_per_minute", "pihole_url", "mqtt_host", "mqtt_port",
})  # fmt: skip
SECRETS = ("mqtt_password", "fritzbox_password", "pihole_password", "dashboard_secret_key",
           "assistant_api_key", "assistant_deepl_key", "assistant_paperless_token",
           "assistant_nextcloud_password", "whatsapp_access_token",
           "whatsapp_app_secret", "whatsapp_verify_token", "discord_bot_token",
           "email_password", "slack_webhook_url", "rocketchat_webhook_url", "matrix_token",
           "mastodon_token", "backup_passphrase", "backup_nextcloud_password")  # fmt: skip


def to_env(field_: Field, value: Any) -> str:
    """Validate a submitted value for one field and render it for the env file."""
    if field_.kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{field_.key}: expected true or false")
        return "true" if value else "false"
    if field_.kind == "int":
        number = int(value)
        if (field_.minimum is not None and number < field_.minimum) or (
            field_.maximum is not None and number > field_.maximum
        ):
            raise ValueError(f"{field_.key}: {field_.minimum}-{field_.maximum}")
        return str(number)
    if field_.kind == "language":
        if value not in LANGUAGES:
            raise ValueError(f"{field_.key}: not available")
        return str(value)
    if field_.kind == "choice":
        if value not in field_.options:
            raise ValueError(f"{field_.key}: one of {', '.join(field_.options)}")
        return str(value)
    if field_.kind == "multi":
        items = value if isinstance(value, list) else str(value).split(",")
        if not items or not set(items) <= set(field_.options):
            raise ValueError(f"{field_.key}: choose from {', '.join(field_.options)}")
        return ",".join(items)
    if field_.kind == "secret":
        text = str(value).strip()
        if not text or "\n" in text or len(text) > 200:
            raise ValueError(f"{field_.key}: invalid value")
        if field_.key == "discord_bot_token" and text.removeprefix("Bot ").count(".") != 2:
            raise ValueError("web.discord_bad_token")  # the Client Secret looks similar
        return text
    if field_.kind == "list":
        items = value if isinstance(value, list) else str(value).split(",")
        return ",".join(str(i).strip() for i in items if str(i).strip())
    text = str(value).strip()
    if "\n" in text or len(text) > 300:
        raise ValueError(f"{field_.key}: invalid text")
    return text
