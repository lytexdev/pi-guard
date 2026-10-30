from __future__ import annotations

from pi_guard.messaging.commands import admin, core, faces, modes
from pi_guard.messaging.commands.registry import Registry


def build_registry() -> Registry:
    registry = Registry()
    for module in (core, modes, admin, faces):
        module.register(registry)
    return registry
