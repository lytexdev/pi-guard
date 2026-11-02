from __future__ import annotations

from pi_guard.messaging.commands import admin, audio, core, faces, images, modes
from pi_guard.messaging.commands.registry import Registry


def build_registry() -> Registry:
    registry = Registry()
    for module in (core, images, modes, admin, faces, audio):
        module.register(registry)
    return registry
