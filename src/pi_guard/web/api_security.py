"""Security analysis: what happened at the door, and what could be better."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import timedelta
from typing import Any

from aiohttp import web

from pi_guard.core.timeutil import utcnow
from pi_guard.network.lan import neighbour_table
from pi_guard.web.server import dashboard

WINDOW_DAYS = 7
SEEN_KEY = "lan.first_seen"
NEW_DEVICE_DAYS = 7


def routes() -> list[web.RouteDef]:
    return [web.get("/api/security", analysis)]


def _audit_since(app: Any, days: int) -> list[dict[str, str]]:
    since = (utcnow() - timedelta(days=days)).isoformat()
    rows = app.db.execute(
        "SELECT ts, actor, action, detail FROM audit WHERE ts >= ? ORDER BY id DESC LIMIT 5000",
        (since,),
    ).fetchall()
    return [dict(r) for r in rows]


def _source(row: dict[str, str]) -> str:
    """The address in entries like `web:1.2.3.4` or `1.2.3.4: /wp-admin`."""
    if row["actor"].startswith("web:"):
        return row["actor"][4:]
    return row["detail"].split(":", 1)[0] if ":" in row["detail"] else row["actor"]


def track_devices(app: Any, macs: list[str]) -> list[dict[str, str]]:
    """Remember when each device was first seen; returns the recent newcomers."""
    seen: dict[str, str] = app.db.get(SEEN_KEY) or {}
    first_run = not seen
    now = utcnow()
    for mac in macs:
        if mac not in seen:
            seen[mac] = "" if first_run else now.isoformat()
    app.db.set(SEEN_KEY, seen)
    limit = (now - timedelta(days=NEW_DEVICE_DAYS)).isoformat()
    return [{"mac": m, "first_seen": ts} for m, ts in seen.items() if ts and ts >= limit]


def recommendations(d: Any, account: dict[str, Any]) -> list[dict[str, str]]:
    s = d.settings
    tips = []
    strong_login = account["totp"] or d.auth.credentials()
    if not strong_login:
        tips.append({"key": "web.sec_tip_2fa", "href": "#/settings/access"})
    if account["totp"] and not account["recovery_left"]:
        tips.append({"key": "web.sec_tip_recovery", "href": "#/settings/access"})
    if s.dashboard_public_url and not strong_login:
        tips.append({"key": "web.sec_tip_public_2fa", "href": "#/settings/access"})
    if not s.dashboard_honeypot:
        tips.append({"key": "web.sec_tip_honeypot", "href": "#/settings/dashboard"})
    if not s.dashboard_tls and not s.dashboard_public_url:
        tips.append({"key": "web.sec_tip_tls", "href": "#/settings/dashboard"})
    return tips


async def analysis(request: web.Request) -> web.Response:
    d = dashboard(request)
    app = d.app
    rows = await asyncio.to_thread(_audit_since, app, WINDOW_DAYS)
    counts = Counter(r["action"] for r in rows)
    failed = [r for r in rows if r["action"] in ("login.failed", "login.totp_failed")]
    honeypot = [r for r in rows if r["action"] == "security.honeypot"]
    attackers = Counter(_source(r) for r in [*failed, *honeypot]
                        if r["action"] != "security.bad_host")  # fmt: skip
    paths = Counter(r["detail"].split(": ", 1)[-1].replace("honeypot ", "") for r in honeypot)
    try:
        neighbours = await neighbour_table(app.config.file.presence.interface)
    except (OSError, TimeoutError):
        neighbours = {}
    newcomers = track_devices(app, list(neighbours))
    for item in newcomers:
        item["ip"] = neighbours.get(item["mac"], ("", ""))[0]
    blocked = None
    if app.pihole is not None:
        from pi_guard.network.pihole import PiholeError

        try:
            blocked = (await app.pihole.summary())["percent_blocked"]
        except PiholeError:
            blocked = None
    interesting = (
        "login", "login.failed", "login.totp_failed", "login.telegram", "login.webauthn",
        "login.recovery_code", "security.ban", "security.honeypot", "security.bad_host", "denied",
        "rejected", "remote.run", "account.credentials", "account.totp_off",
        "account.webauthn_add", "account.recovery_codes", "account.codeword",
    )  # fmt: skip
    return web.json_response({
        "days": WINDOW_DAYS,
        "counts": {
            "logins": sum(counts[k] for k in ("login", "login.telegram", "login.webauthn")),
            "failed_logins": len(failed),
            "honeypot_hits": len(honeypot),
            "bans": counts["security.ban"],
            "bad_hosts": counts["security.bad_host"],
            "rejected_messages": counts["rejected"],
            "denied_commands": counts["denied"],
            "recovery_used": counts["login.recovery_code"],
        },
        "top_sources": [{"ip": ip, "count": n} for ip, n in attackers.most_common(8)],
        "honeypot_paths": [{"path": p, "count": n} for p, n in paths.most_common(8)],
        "active_bans": d.guard.bans(),
        "sessions": len(d.auth.sessions()),
        "new_devices": newcomers,
        "percent_blocked": blocked,
        "recommendations": recommendations(d, d.auth.account()),
        "timeline": [r for r in rows if r["action"] in interesting][:40],
    })  # fmt: skip
