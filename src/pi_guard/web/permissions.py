"""What a member may do: areas, add-ons and settings sections the owner ticked.

Three roles: admins may do everything, viewers only watch (VIEWER_* in server.py), and members
watch plus whatever is granted here. Only paths listed below can be granted at all: anything
else, also everything added later, stays with admins. Access, logins, tokens, backups, updates,
the security check and own add-ons are never granted.
"""

from __future__ import annotations

from collections.abc import Iterable

# area -> path prefixes it opens (all methods).
AREAS: dict[str, tuple[str, ...]] = {
    "control": ("/api/mode", "/api/alarm", "/api/alerts", "/api/talk", "/api/say", "/api/volume",
                "/api/schedule"),
    "people": ("/api/people", "/api/people-", "/api/faces/", "/api/image/"),
    "network": ("/api/network", "/api/privacy/", "/api/topology"),
    "logs": ("/api/logs", "/api/audit", "/api/security"),
    "contacts": ("/api/contacts", "/api/notifications"),
    "devices": ("/api/devices", "/api/sounds", "/api/calendars"),
    "apps": ("/api/apps", "/api/mobile/devices", "/api/mobile/app"),
}  # fmt: skip
ADDONS: dict[str, tuple[str, ...]] = {
    "assistant": ("/api/assistant/", "/api/addons/assistant"),
    "medical": ("/api/medical",),
    "kids": ("/api/kids",),
    "agents": ("/api/agents",),
    "remote": ("/api/addons/remote",),
    "smarthome": ("/api/addons/smarthome",),
    "analytics": ("/api/addons/analytics",),
    "heartbeat": ("/api/addons/heartbeat",),
    "languages": ("/api/addons/languages",),
    "proxy": ("/api/proxy",),
}
# settings section -> the groups of fields in it (settings_schema.py).
SETTINGS: dict[str, tuple[str, ...]] = {
    "general": ("general",),
    "recording": ("recording",),
    "detection": ("detection",),
    "modes": ("modes",),
    "audio": ("audio",),
    "messaging": ("telegram", "discord", "email", "whatsapp", "signal", "chatapps"),
    "emergency": ("emergency",),
    "apps": ("mobile",),
}
ALL = (*AREAS, *(f"addon:{a}" for a in ADDONS), *(f"settings:{s}" for s in SETTINGS))
MAX = len(ALL)


def clean(permissions: Iterable[object]) -> list[str]:
    """Known names only, each once, in the order of ALL."""
    wanted = {str(p) for p in permissions}
    return [p for p in ALL if p in wanted]


def settings_groups(permissions: Iterable[str]) -> set[str]:
    groups: set[str] = set()
    for permission in permissions:
        if permission.startswith("settings:"):
            groups.update(SETTINGS.get(permission.removeprefix("settings:"), ()))
    return groups


def _under(path: str, prefix: str) -> bool:
    """By whole path segments: "/api/mode" opens "/api/mode/x", never "/api/models"."""
    if prefix.endswith(("/", "-")):
        return path.startswith(prefix)
    return path == prefix or path.startswith(prefix + "/")


def allowed(permissions: Iterable[str], method: str, path: str) -> bool:
    """Whether a member with these permissions may make this request."""
    granted = set(permissions)
    if method not in ("GET", "HEAD") and path.startswith("/api/events/"):
        return "people" in granted  # "this is X" under an event teaches faces
    for area, prefixes in AREAS.items():
        if area in granted and any(_under(path, p) for p in prefixes):
            return True
    for addon, prefixes in ADDONS.items():
        if f"addon:{addon}" in granted and any(_under(path, p) for p in prefixes):
            return True
    reading = method in ("GET", "HEAD")
    if reading and path in ("/api/addons", "/api/modules"):  # the list, to find their add-ons
        return any(p.startswith("addon:") for p in granted)
    # The settings handler shows and takes only the granted groups.
    return path == "/api/settings" and bool(settings_groups(granted))
