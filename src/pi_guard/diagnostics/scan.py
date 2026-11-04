"""Security scan. What pi-guard can see itself is checked here; everything that needs root
comes from the job pi-guard-maint, which only ever receives one of a few fixed words."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import importlib.metadata
import json
import logging
import stat
import time
from pathlib import Path
from typing import Any

from pi_guard.core.timeutil import utcnow
from pi_guard.security.files import atomic_write

log = logging.getLogger(__name__)

HELPER = Path("/usr/local/sbin/pi-guard-maint")
REPORTS = Path("/var/lib/pi-guard-scan")
LAST_KEY = "scan.last"
ROOT_TIMEOUT = 30 * 60
POLL_SECONDS = 3
EXPECTED_PORTS = {22, 53, 67, 80, 443, 546, 5353, 8443, 51820}
EPHEMERAL_PORTS = 32768
ORDER = {"bad": 0, "warn": 1, "unknown": 2, "ok": 3}


def check(key: str, status: str, detail: Any = "") -> dict[str, Any]:
    return {"id": key, "status": status, "detail": str(detail)}


def request_root(data_dir: Path, word: str) -> None:
    atomic_write(data_dir / "maint" / "request", word.encode(), 0o600)


def read_report(name: str) -> dict[str, Any] | None:
    try:
        return dict(json.loads((REPORTS / f"{name}.json").read_text()))
    except (OSError, ValueError):
        return None


def package_integrity() -> dict[str, Any]:
    """Compare pi-guard's own files with the hashes recorded when it was installed."""
    changed: list[str] = []
    checked = 0
    for entry in importlib.metadata.files("pi-guard") or []:
        if entry.hash is None or entry.hash.mode != "sha256":
            continue
        checked += 1
        try:
            digest = hashlib.sha256(Path(str(entry.locate())).read_bytes()).digest()
        except OSError:
            changed.append(str(entry))
            continue
        if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != entry.hash.value:
            changed.append(str(entry))
    return {"checked": checked, "changed": changed}


async def listening_ports() -> list[int]:
    process = await asyncio.create_subprocess_exec(
        "ss", "-H", "-ltun", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    output, _ = await process.communicate()
    ports = set()
    for line in output.decode().splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        host, _, port = fields[4].rpartition(":")
        if not port.isdigit() or host.startswith(("127.", "[::1]", "[::ffff:127.")):
            continue
        if fields[0] == "tcp" or int(port) < EPHEMERAL_PORTS:  # UDP clients have a port as well
            ports.add(int(port))
    return sorted(ports)


def own_checks(app: Any, ports: list[int], integrity: dict[str, Any]) -> list[dict[str, Any]]:
    s = app.settings
    auth = app.dashboard.auth if app.dashboard else None
    checks = []
    if auth is not None:
        account = auth.account()
        checks.append(check("password", "bad" if account["must_change"] else "ok"))
        strong = account["totp"] or auth.credentials()
        checks.append(check("second_factor", "ok" if strong else "warn"))
    checks.append(check("tls", "ok" if s.dashboard_tls or not s.dashboard_enabled else "warn"))
    mode = stat.S_IMODE(s.data_dir.stat().st_mode)
    checks.append(check("data_rights", "bad" if mode & 0o007 else "ok", oct(mode)))
    changed = integrity["changed"]
    if not integrity["checked"]:
        checks.append(check("integrity", "unknown"))
    else:
        detail = ", ".join(changed[:5]) if changed else integrity["checked"]
        checks.append(check("integrity", "bad" if changed else "ok", detail))
    extra = [p for p in ports if p not in EXPECTED_PORTS]
    checks.append(check("ports", "warn" if extra else "ok",
                        ", ".join(map(str, (extra or ports)[:15]))))  # fmt: skip
    return checks


def root_checks(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if report is None:
        return [check("root_job", "unknown")]
    checks = []
    updates, security = report.get("updates"), report.get("security_updates")
    if updates is None:
        checks.append(check("updates", "unknown"))
    else:
        checks.append(check("updates", "bad" if security else "warn" if updates else "ok",
                            f"{updates} / {security}"))  # fmt: skip
    checks.append(check("reboot", "warn" if report.get("reboot_required") else "ok"))
    checks.append(check("auto_updates", "ok" if report.get("unattended") else "warn"))
    checks.append(check("firewall", "ok" if report.get("firewall") else "bad",
                        report.get("firewall_detail", "")))  # fmt: skip
    ssh = report.get("ssh")
    if ssh:
        risky = ssh.get("permitrootlogin") == "yes"
        passwords = ssh.get("passwordauthentication") == "yes"
        checks.append(check("ssh", "bad" if risky else "warn" if passwords else "ok",
                            ", ".join(f"{k}={v}" for k, v in ssh.items())))  # fmt: skip
    files = report.get("changed_files") or []
    checks.append(check("system_files", "warn" if files else "ok", ", ".join(files[:5])))
    suid = report.get("unknown_suid") or []
    checks.append(check("suid", "bad" if suid else "ok", ", ".join(suid[:5])))
    odd = [*report.get("preload", []), *report.get("tmp_programs", [])]
    checks.append(check("suspicious", "bad" if odd else "ok", ", ".join(odd[:5])))
    admins = report.get("extra_root_users") or []
    empty = report.get("empty_passwords") or 0
    checks.append(check("accounts", "bad" if admins or empty else "ok", ", ".join(admins)))
    tool = report.get("rootkit_tool")
    if tool is None:
        checks.append(check("rootkits", "unknown"))
    else:
        warnings = report.get("rootkit_warnings") or []
        checks.append(check("rootkits", "bad" if warnings else "ok",
                            "; ".join([tool, *warnings[:3]])))  # fmt: skip
    return checks


class SecurityScan:
    def __init__(self, app: Any) -> None:
        self._app = app
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def last(self) -> dict[str, Any] | None:
        report: dict[str, Any] | None = self._app.db.get(LAST_KEY)
        return report

    def start(self, report_to_admins: bool) -> bool:
        if self.running:
            return False
        self._task = asyncio.create_task(self._guarded(report_to_admins), name="security-scan")
        return True

    async def _guarded(self, report_to_admins: bool) -> None:
        try:
            await self._run(report_to_admins)
        except Exception:
            log.exception("security scan failed")

    async def _root(self) -> dict[str, Any] | None:
        if not HELPER.exists():
            return None
        begun = time.time()
        request_root(self._app.settings.data_dir, "scan")
        while time.time() - begun < ROOT_TIMEOUT:
            await asyncio.sleep(POLL_SECONDS)
            with contextlib.suppress(OSError):
                if (REPORTS / "scan.json").stat().st_mtime >= begun:
                    return read_report("scan")
        return None

    async def _run(self, report_to_admins: bool) -> None:
        integrity = await asyncio.to_thread(package_integrity)
        ports = await listening_ports()
        checks = own_checks(self._app, ports, integrity) + root_checks(await self._root())
        checks.sort(key=lambda c: ORDER[c["status"]])
        counts = {k: sum(c["status"] == k for c in checks) for k in ORDER}
        report = {"time": utcnow().isoformat(), "checks": checks, "counts": counts}
        self._app.db.set(LAST_KEY, report)
        self._app.db.audit("system", "security.scan", json.dumps(counts))
        if report_to_admins:
            await self._send(report)

    async def _send(self, report: dict[str, Any]) -> None:
        from pi_guard.i18n import t

        lang = self._app.settings.language
        counts = report["counts"]
        lines = [t("scan.report", lang, bad=counts["bad"], warn=counts["warn"], ok=counts["ok"])]
        for item in report["checks"]:
            if item["status"] in ("bad", "warn"):
                mark = "❗" if item["status"] == "bad" else "⚠️"
                lines.append(f"{mark} {t('web.scan_' + item['id'], lang)}")
        await self._app.messenger.notify_admins("\n".join(lines))
