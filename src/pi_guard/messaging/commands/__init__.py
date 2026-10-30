from __future__ import annotations

from pi_guard.messaging.commands import admin, core, modes
from pi_guard.messaging.commands.registry import Registry


def build_registry() -> Registry:
    registry = Registry()
    for module in (core, modes, admin):
        module.register(registry)
    return registry
