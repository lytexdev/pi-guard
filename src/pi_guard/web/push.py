"""Web Push to installed dashboard apps (VAPID, RFC 8030/8291/8292).

The payload is encrypted for the receiving browser, but it is delivered through
the browser vendor's push service (Google, Mozilla, Apple). It therefore only
carries the alert text, never photos, names of recognised people or links that
work without logging in.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any

from pi_guard.core.timeutil import utcnow
from pi_guard.security.files import secure_file
from pi_guard.storage.db import Database

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint TEXT PRIMARY KEY, p256dh TEXT NOT NULL, auth TEXT NOT NULL,
    language TEXT NOT NULL, username TEXT NOT NULL, created TEXT NOT NULL);
"""
TTL_SECONDS = 3600
GONE = (404, 410)


class WebPush:
    def __init__(self, db: Database, key_path: Path, contact: str) -> None:
        from py_vapid import Vapid01

        self._db = db
        self._contact = contact
        db.executescript(_SCHEMA)
        if not key_path.exists():
            vapid = Vapid01()
            vapid.generate_keys()
            key_path.parent.mkdir(parents=True, exist_ok=True)
            vapid.save_key(str(key_path))
            secure_file(key_path)
        self._key_path = key_path
        self._vapid = Vapid01.from_file(str(key_path))

    @property
    def public_key(self) -> str:
        from cryptography.hazmat.primitives import serialization

        raw = self._vapid.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def subscribe(self, subscription: dict[str, Any], language: str, username: str) -> None:
        endpoint = str(subscription.get("endpoint", ""))
        keys = subscription.get("keys") or {}
        if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
            raise ValueError("invalid push subscription")
        self._db.execute(
            "INSERT OR REPLACE INTO push_subscriptions VALUES (?, ?, ?, ?, ?, ?)",
            (endpoint[:1000], str(keys["p256dh"])[:200], str(keys["auth"])[:100],
             language if language in ("de", "en") else "en", username, utcnow().isoformat()),
        )  # fmt: skip

    def unsubscribe(self, endpoint: str) -> bool:
        return (
            self._db.execute(
                "DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
            ).rowcount
            > 0
        )

    def subscriptions(self) -> list[dict[str, str]]:
        return [dict(r) for r in self._db.execute("SELECT * FROM push_subscriptions")]

    async def send(self, texts: dict[str, dict[str, str]]) -> int:
        """`texts` maps language -> payload; returns the number of devices reached."""
        delivered = 0
        for sub in self.subscriptions():
            payload = texts.get(sub["language"]) or next(iter(texts.values()))
            ok = await asyncio.to_thread(self._send_one, sub, payload)
            delivered += ok
        return delivered

    def _send_one(self, sub: dict[str, str], payload: dict[str, str]) -> bool:
        from pywebpush import WebPushException, webpush

        info = {"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}}
        try:
            webpush(
                subscription_info=info,
                data=json.dumps(payload),
                vapid_private_key=str(self._key_path),
                vapid_claims={"sub": self._contact},
                ttl=TTL_SECONDS,
                timeout=10,
            )
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in GONE:
                self.unsubscribe(sub["endpoint"])  # the browser dropped this subscription
            else:
                log.warning("push delivery failed: %s", status or exc)
            return False
        except Exception as exc:
            log.warning("push delivery failed: %s", type(exc).__name__)
            return False
        return True
