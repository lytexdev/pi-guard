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
# The root job's steps each have their own time limit (the longest 25 minutes). It reports after
# each one, so pi-guard waits as long as it hears of progress, up to ROOT_TIMEOUT in all.
ROOT_IDLE = 30 * 60
ROOT_TIMEOUT = 2 * 60 * 60
POLL_SECONDS = 3
EXPECTED_PORTS = {22, 53, 67, 80, 443, 546, 5353, 8443, 51820}
EPHEMERAL_PORTS = 32768
ORDER = {"bad": 0, "warn": 1, "unknown": 2, "ok": 3}
# Stable order for the live progress UI: own checks, then those of the root job.
OWN_IDS = ("password", "second_factor", "tls", "data_rights", "integrity", "ports")
ROOT_IDS = (
    "updates",
    "reboot",
    "auto_updates",
    "firewall",
    "ssh",
    "system_files",
    "suid",
    "suspicious",
    "accounts",
    "rootkits",
)
PROGRESS_IDS = OWN_IDS + ROOT_IDS
# The root job's steps (functions in pi-guard-maint) and the checks each one answers.
STEP_CHECKS = {
    "updates": ("updates", "reboot", "auto_updates"),
    "firewall": ("firewall",),
    "ssh": ("ssh",),
    "files": ("system_files", "suid"),
    "suspicious": ("suspicious",),
    "accounts": ("accounts",),
    "rootkits": ("rootkits",),
}


def check(key: str, status: str, detail: Any = "", fix: str | None = None) -> dict[str, Any]:
    """`fix` names the advice when it differs from the usual web.scan_<key>_fix."""
    item = {"id": key, "status": status, "detail": str(detail)}
    if fix:
        item["fix"] = fix
    return item


# rkhunter warnings that every pi-guard has: Pi-hole keeps its tables in /dev/shm, and the
# install adds the pi-guard user to groups such as gpio. Shown as known, not as a finding.
_HARMLESS = ("/dev/shm/FTL-",  # noqa: S108 - a name to match, not a file we use
             "User 'pi-guard' has been added to the group",
             "Suspicious file types found in /dev:",
             "Changes found in the group file for group")  # fmt: skip


def rootkit_findings(lines: list[str]) -> tuple[list[str], int]:
    """(warnings worth a look, how many known harmless ones were left out)."""
    left = [x for x in lines if x.strip()]
    kept = [x for x in left if not any(h in x for h in _HARMLESS)]
    return kept, len(left) - len(kept)


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
    if ssh == {}:
        checks.append(check("ssh", "ok", "no SSH server"))
    elif ssh:
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
        checks.append(check("rootkits", "unknown", fix="web.scan_rootkits_missing_fix"))
    else:
        warnings, harmless = rootkit_findings(report.get("rootkit_warnings") or [])
        note = f"{tool}" + (f" (+{harmless} known)" if harmless else "")
        checks.append(check("rootkits", "bad" if warnings else "ok",
                            "; ".join([note, *warnings[:3]]),
                            fix="web.scan_rootkits_found_fix"))  # fmt: skip
    return _unfinished(checks, report)


def _unfinished(checks: list[dict[str, Any]], report: dict[str, Any]) -> list[dict[str, Any]]:
    """A step that crashed or whose commands timed out found nothing because it did not look:
    its checks say unknown, with the reason, instead of ok."""
    why: dict[str, str] = {}
    for entry in report.get("failed_steps") or []:
        step, _, error = str(entry).partition(": ")
        why[step] = error or "failed"
    for step, problems in (report.get("incomplete_steps") or {}).items():
        why.setdefault(step, "; ".join(map(str, problems))[:160])
    for step, reason in why.items():
        for key in STEP_CHECKS.get(step, ()):
            found = next((c for c in checks if c["id"] == key), None)
            item = check(key, "unknown", reason, fix="web.scan_unfinished_fix")
            if found is None:
                checks.append(item)
            elif found["status"] in ("ok", "unknown"):  # a finding stays a finding
                found.update(item)
    return checks


class SecurityScan:
    def __init__(self, app: Any) -> None:
        self._app = app
        self._task: asyncio.Task[None] | None = None
        # Shown as running: the own checks together, then the root job's together. The root job
        # reports only at its end, so the page must not pretend to know which of them it is on.
        self._current: list[str] = []
        self._done: list[str] = []

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def last(self) -> dict[str, Any] | None:
        report: dict[str, Any] | None = self._app.db.get(LAST_KEY)
        return report

    @property
    def progress(self) -> dict[str, Any] | None:
        if not self.running:
            return None
        return {
            "current": self._current,
            "done": list(self._done),
            "checks": list(PROGRESS_IDS),
        }

    def start(self, report_to_admins: bool) -> bool:
        if self.running:
            return False
        self._current = list(OWN_IDS)
        self._done = []
        self._task = asyncio.create_task(self._guarded(report_to_admins), name="security-scan")
        return True

    async def _guarded(self, report_to_admins: bool) -> None:
        try:
            await self._run(report_to_admins)
        except Exception:
            log.exception("security scan failed")
        finally:
            self._current = []
            self._done = []

    def _follow(self, progress: dict[str, Any] | None, begun: float) -> bool:
        """Show the root job's real step. True when it reported since the scan began."""
        if not progress or float(progress.get("started", 0)) < int(begun):
            return False
        done = [k for step in progress.get("done") or [] for k in STEP_CHECKS.get(step, ())]
        self._done = [*OWN_IDS, *done]
        self._current = list(STEP_CHECKS.get(str(progress.get("step")), ()))
        return True

    async def _root(self) -> dict[str, Any] | None:
        if not HELPER.exists():
            return None
        begun = time.time()
        heard = begun
        last_step: Any = None
        request_root(self._app.settings.data_dir, "scan")
        while time.time() - begun < ROOT_TIMEOUT and time.time() - heard < ROOT_IDLE:
            await asyncio.sleep(POLL_SECONDS)
            progress = read_report("scan-progress")
            if progress is not None and self._follow(progress, begun):
                step = (progress.get("step"), len(progress.get("done") or []))
                if step != last_step:
                    last_step, heard = step, time.time()
            with contextlib.suppress(OSError):
                if (REPORTS / "scan.json").stat().st_mtime >= begun:
                    return read_report("scan")
        log.warning("security scan: the root job did not finish")
        return None

    async def _run(self, report_to_admins: bool) -> None:
        integrity = await asyncio.to_thread(package_integrity)
        ports = await listening_ports()
        own = own_checks(self._app, ports, integrity)
        self._done = list(OWN_IDS)
        self._current = list(ROOT_IDS) if HELPER.exists() else []
        root = await self._root()
        checks = own + root_checks(root)
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
