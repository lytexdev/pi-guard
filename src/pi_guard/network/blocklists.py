"""Reading block lists: hosts files, plain domain lists and Adblock-style `||domain^` rules."""

from __future__ import annotations

import re

MAX_FILE_BYTES = 5_000_000
MAX_DOMAINS = 50_000
_DOMAIN_RE = re.compile(
    r"^(?=.{4,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$"
)
_ADBLOCK_RE = re.compile(r"^\|\|([^\^/$]+)\^")
_NEVER = {"localhost", "localhost.localdomain", "local", "broadcasthost", "ip6-localhost"}

# Well-known lists, offered with one click.
PRESETS = (
    {"id": "hagezi_pro", "kind": "ads", "name": "HaGeZi Pro",
     "address": "https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/hosts/pro.txt"},
    {"id": "hagezi_tif", "kind": "malware", "name": "HaGeZi Threat Intelligence",
     "address": "https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/hosts/tif.txt"},
    {"id": "urlhaus", "kind": "malware", "name": "URLhaus (abuse.ch)",
     "address": "https://urlhaus.abuse.ch/downloads/hostfile/"},
    {"id": "phishing", "kind": "phishing", "name": "Phishing Filter",
     "address": "https://malware-filter.gitlab.io/malware-filter/phishing-filter-hosts.txt"},
)  # fmt: skip


def valid_list_address(address: str) -> bool:
    return bool(re.match(r"^https?://[^\s]{4,500}$", address))


def parse_domains(text: str) -> tuple[list[str], int]:
    """(domains, lines that were not understood). Comments and empty lines do not count."""
    domains: dict[str, None] = {}
    bad = 0
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().casefold()
        if not line or line.startswith(("!", "[")):
            continue
        adblock = _ADBLOCK_RE.match(line)
        if adblock:
            candidate = adblock.group(1)
        else:
            parts = line.split()
            # hosts format: "0.0.0.0 example.org"
            candidate = parts[1] if len(parts) >= 2 and parts[0][0].isdigit() else parts[0]
        if candidate in _NEVER:
            continue
        if _DOMAIN_RE.match(candidate):
            domains[candidate] = None
            if len(domains) >= MAX_DOMAINS:
                break
        else:
            bad += 1
    return list(domains), bad
