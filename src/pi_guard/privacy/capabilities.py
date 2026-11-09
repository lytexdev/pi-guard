"""Feature registry: what this Pi can run, and what each feature costs."""

from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Performance impact labels shown in the UI. Estimates, not measurements.
PERF_MINIMAL = "minimal"
PERF_LOW = "low"
PERF_MODERATE = "moderate"
PERF_HIGH = "high"
PERF_VARIABLE = "variable"


@dataclass(frozen=True)
class Capability:
    id: str
    available: bool
    platforms: tuple[str, ...]
    dependencies: tuple[str, ...]
    performance: str
    reason: str | None = None  # translation key when unavailable

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _has_cmd(*names: str) -> bool:
    return any(shutil.which(name) for name in names)


def _amnezia_present() -> bool:
    """AmneziaWG needs a custom kernel module or userspace binary; stock Pi has neither."""
    if Path("/sys/module/amneziawg").is_dir():
        return True
    return _has_cmd("awg", "amneziawg-go")


def _xray_present() -> bool:
    return _has_cmd("xray")


def _shadowsocks_present() -> bool:
    return _has_cmd("ssserver", "ss-server", "shadowsocks-libev")


def detect(data_dir: Path | None = None) -> dict[str, Capability]:
    """Probe the host once per request. Disabled features stay cheap: no services start."""
    del data_dir  # reserved for future probes that need state
    tor = _has_cmd("tor")
    wg = _has_cmd("wg")
    nft = _has_cmd("nft")
    amnezia = _amnezia_present()
    return {
        "home_vpn": Capability("home_vpn", True, ("linux",), ("wireguard",), PERF_MINIMAL),
        "post_quantum": Capability(
            "post_quantum",
            True,
            ("linux", "android", "windows"),
            ("vpn/kem",),
            PERF_LOW,
        ),
        "custom_exit": Capability(
            "custom_exit",
            wg,
            ("linux",),
            ("wireguard", "wg"),
            PERF_LOW,
            None if wg else "web.priv_need_wg",
        ),
        "mullvad": Capability(
            "mullvad",
            wg,
            ("linux",),
            ("wireguard", "network"),
            PERF_LOW,
            None if wg else "web.priv_need_wg",
        ),
        "import_wg": Capability(
            "import_wg",
            wg,
            ("linux",),
            ("wireguard",),
            PERF_LOW,
            None if wg else "web.priv_need_wg",
        ),
        "multi_hop": Capability(
            "multi_hop",
            wg and nft,
            ("linux",),
            ("wireguard", "nftables"),
            PERF_MODERATE,
            None if (wg and nft) else "web.priv_need_wg_nft",
        ),
        "kill_switch": Capability(
            "kill_switch",
            nft,
            ("linux",),
            ("nftables",),
            PERF_MINIMAL,
            None if nft else "web.priv_need_nft",
        ),
        "dns_privacy": Capability(
            "dns_privacy",
            True,
            ("linux",),
            (),
            PERF_LOW,
        ),
        "dns_dot": Capability(
            "dns_dot",
            _has_cmd("resolvectl") or Path("/etc/unbound/unbound.conf").is_file(),
            ("linux",),
            ("systemd-resolved|unbound",),
            PERF_LOW,
            None
            if (_has_cmd("resolvectl") or Path("/etc/unbound/unbound.conf").is_file())
            else "web.priv_need_dot",
        ),
        "dns_doh": Capability(
            "dns_doh",
            False,
            ("linux",),
            ("local-resolver",),
            PERF_LOW,
            "web.priv_doh_later",
        ),
        "dns_doq": Capability(
            "dns_doq",
            False,
            ("linux",),
            ("local-resolver",),
            PERF_LOW,
            "web.priv_doq_later",
        ),
        "tor": Capability(
            "tor",
            tor,
            ("linux",),
            ("tor",),
            PERF_HIGH,
            None if tor else "web.priv_need_tor",
        ),
        "tor_bridges": Capability(
            "tor_bridges",
            tor
            and (
                Path("/usr/bin/obfs4proxy").is_file()
                or Path("/usr/bin/snowflake-client").is_file()
                or _has_cmd("obfs4proxy", "snowflake-client")
            ),
            ("linux",),
            ("tor", "obfs4proxy|snowflake"),
            PERF_HIGH,
            None if tor else "web.priv_need_tor",
        ),
        "amneziawg": Capability(
            "amneziawg",
            amnezia,
            ("linux",),
            ("amneziawg",),
            PERF_MODERATE,
            None if amnezia else "web.priv_need_amnezia",
        ),
        "shadowsocks": Capability(
            "shadowsocks",
            _shadowsocks_present(),
            ("linux",),
            ("shadowsocks",),
            PERF_MODERATE,
            "web.priv_ss_disabled" if not _shadowsocks_present() else None,
        ),
        "xray_reality": Capability(
            "xray_reality",
            _xray_present(),
            ("linux",),
            ("xray",),
            PERF_MODERATE,
            "web.priv_xray_disabled" if not _xray_present() else None,
        ),
        "ssh_provision": Capability(
            "ssh_provision",
            _has_cmd("ssh"),
            ("linux",),
            ("openssh-client",),
            PERF_VARIABLE,
            None if _has_cmd("ssh") else "web.priv_need_ssh",
        ),
        "diagnostics": Capability(
            "diagnostics",
            True,
            ("linux",),
            (),
            PERF_MINIMAL,
        ),
        "profiles": Capability(
            "profiles",
            True,
            ("linux",),
            (),
            PERF_MINIMAL,
        ),
    }


def installed_marker(data_dir: Path) -> bool:
    """True when the owner ran scripts/privacy-server.sh install."""
    marker = Path(
        os.environ.get("PI_GUARD_PRIVACY_INSTALLED", "/run/pi-guard-status/privacy.installed")
    )
    if marker.is_file():
        return True
    return (data_dir / "privacy" / "installed").is_file()
