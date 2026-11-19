"""Who to ask for help: from the env file (`PI_GUARD_SUPPORT_*`), set there or by install.sh.

An older dashboard stored it in the database; that is shown until the env file names someone.
"""

from __future__ import annotations

from typing import Any

FIELDS = ("name", "phone", "email", "address")


def support_contact(settings: Any, db: Any) -> dict[str, str]:
    own = {key: str(getattr(settings, f"support_{key}", "") or "") for key in FIELDS}
    if any(own.values()):
        return own
    old = db.get("web.support") or {}
    return {key: str(value) for key, value in old.items() if key in FIELDS}
