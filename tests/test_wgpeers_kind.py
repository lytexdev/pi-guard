"""VPN devices know what they are, for their icon."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pi_guard.network.wgpeers import PeerError, PeerStore, guess_kind


def test_a_name_tells_the_kind() -> None:
    assert guess_kind("Pixel Anna") == "android"
    assert guess_kind("iPhone Ben") == "ios"
    assert guess_kind("DESKTOP-AB12CD3") == "windows"
    assert guess_kind("MacBook Air") == "macos"
    assert guess_kind("ubuntu-box") == "linux"
    assert guess_kind("Handy Oma") == "phone"
    assert guess_kind("Laptop Papa") == "computer"
    assert guess_kind("Fernseher") == "other"


def test_the_kind_is_stored_and_checked(db: Any, tmp_path: Path) -> None:
    store = PeerStore(db, tmp_path, b"k" * 32)
    first = store.add("Anna", kind="android")
    second = store.add("Ben")
    kinds = {p["name"]: p["kind"] for p in store.list()}
    assert kinds == {"Anna": "android", "Ben": ""}
    assert store.set_kind(second, "windows") and not store.set_kind(999, "linux")
    assert {p["id"]: p["kind"] for p in store.list()}[second] == "windows"
    with pytest.raises(PeerError):
        store.set_kind(first, "toaster")
    with pytest.raises(PeerError):
        store.add("Carl", kind="<script>")


def test_an_old_table_gets_the_column(db: Any, tmp_path: Path) -> None:
    db.execute(
        "CREATE TABLE wg_peers (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, "
        "address TEXT NOT NULL UNIQUE, public_key TEXT NOT NULL UNIQUE, "
        "private_key TEXT NOT NULL, preshared_key TEXT NOT NULL, "
        "full_tunnel INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL)"
    )
    store = PeerStore(db, tmp_path, b"k" * 32)
    store.add("Anna")
    assert store.list()[0]["kind"] == ""
