"""Orchestrates privacy config, providers, and files for root jobs."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pi_guard.privacy import chain as chain_mod
from pi_guard.privacy import dns as dns_mod
from pi_guard.privacy import obfuscation as obfs_mod
from pi_guard.privacy.capabilities import detect, installed_marker
from pi_guard.privacy.compat import validate_kill_switch
from pi_guard.privacy.errors import PrivacyError
from pi_guard.privacy.firewall import Plan, render
from pi_guard.privacy.health import snapshot
from pi_guard.privacy.providers.custom_wg import CustomWireGuard
from pi_guard.privacy.providers.mullvad import MullvadProvider
from pi_guard.privacy.store import PrivacyStore

log = logging.getLogger(__name__)


class PrivacyService:
    def __init__(
        self, data_dir: Path, secret_key: bytes, *, home_network: str | None = None
    ) -> None:
        self.store = PrivacyStore(data_dir, secret_key)
        self.home_network = home_network
        self.custom = CustomWireGuard()
        self.mullvad = MullvadProvider()
        self._data_dir = data_dir

    def capabilities(self) -> dict[str, Any]:
        caps = detect(self._data_dir)
        return {
            "installed": installed_marker(self._data_dir),
            "features": {k: v.as_dict() for k, v in caps.items()},
        }

    def config(self) -> dict[str, Any]:
        return self.store.public_view()

    def set_kill_switch(self, mode: str) -> dict[str, Any]:
        caps = detect(self._data_dir)
        validate_kill_switch(mode, caps)
        self.store.data["kill_switch"] = mode
        self.store.save()
        self._write_nft()
        return {"kill_switch": mode}

    def set_dns(self, body: dict[str, Any]) -> dict[str, Any]:
        return dns_mod.update(self.store, detect(self._data_dir), body)

    def set_obfuscation(self, body: dict[str, Any]) -> dict[str, Any]:
        return obfs_mod.update(self.store, detect(self._data_dir), body)

    def set_chain(self, hops: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return chain_mod.set_hops(self.store, hops, detect(self._data_dir))

    def add_hop(self, hop: dict[str, Any]) -> dict[str, Any]:
        return chain_mod.add_hop(self.store, hop, detect(self._data_dir))

    def remove_hop(self, hop_id: str) -> None:
        chain_mod.remove_hop(self.store, hop_id, detect(self._data_dir))

    def reorder_hops(self, order: list[str]) -> list[dict[str, Any]]:
        return chain_mod.reorder(self.store, order, detect(self._data_dir))

    def activate(self) -> dict[str, Any]:
        if not installed_marker(self._data_dir):
            raise PrivacyError("web.priv_not_installed")
        data = self.store.data
        hops = [h for h in data["chain"].get("hops") or [] if h.get("enabled") is not False]
        if not hops:
            raise PrivacyError("web.priv_nothing_to_activate")
        peer = chain_mod.build_active_peer(self.store)
        self.store.write_exit_peer(peer.peer_file_line())
        self.store.write_exit_conf(peer.conf_text())
        data["enabled"] = True
        data["chain"]["active"] = True
        self.store.save()
        self._write_nft(exit_up=True)
        return self.diagnostics()

    def deactivate(self) -> dict[str, Any]:
        self.store.data["enabled"] = False
        self.store.data["chain"]["active"] = False
        self.store.clear_exit()
        self.store.save()
        self._write_nft(exit_up=False)
        return self.diagnostics()

    def diagnostics(self, *, exit_ip: str | None = None) -> dict[str, Any]:
        return snapshot(
            self.store,
            exit_ip=exit_ip,
            installed=installed_marker(self._data_dir),
        )

    def _write_nft(self, *, exit_up: bool | None = None) -> None:
        data = self.store.data
        if exit_up is None:
            exit_up = bool(data["chain"].get("active"))
        plan = Plan(
            mode=str(data.get("kill_switch") or "off"),
            exit_up=exit_up and bool(data.get("enabled")),
            allow_lan_mgmt=True,
            home_network=self.home_network,
            home_only=self.store.home_only_addresses(),
        )
        self.store.write_nft_plan(render(plan))
