"""Reverse proxy add-on: tokens kept as hashes, ports only where allowed, pgproxy configs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from pi_guard.addons.proxy import Proxy, ProxyError, fingerprint_of
from pi_guard.storage.db import Database

from .test_web import PASSWORD, _client, _login


@pytest.fixture
def proxy(tmp_path: Path) -> Any:
    db = Database(tmp_path / "pi.db")
    yield Proxy(tmp_path, db)
    db.close()


def test_a_server_keeps_only_the_hash_of_each_token(proxy: Proxy) -> None:
    proxy.set_role("server", 7000)
    token = proxy.add_client("arch", [8080, "8443"])
    state = proxy.state()
    entry = state["clients"][0]
    assert entry["token_sha256"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in json.dumps(state)
    assert token not in json.dumps(proxy.public())
    config = proxy._config(state)
    assert config is not None and config["listen"] == "0.0.0.0:7000"
    assert config["clients"][0]["ports"] == [{"port": 8080, "bind": "0.0.0.0"},
                                             {"port": 8443, "bind": "0.0.0.0"}]  # fmt: skip
    assert (proxy.dir / "key.pem").stat().st_mode & 0o077 == 0
    assert proxy.fingerprint() == fingerprint_of((proxy.dir / "cert.pem").read_text())


def test_ports_names_and_binds_are_checked(proxy: Proxy) -> None:
    proxy.set_role("server", 7000)
    proxy.add_client("pi-1", [9000])
    for name, ports, bind in (
        ("pi-2", [22], "0.0.0.0"),  # below 1024
        ("pi-2", [9000], "0.0.0.0"),  # taken by pi-1
        ("pi-2", [7000], "0.0.0.0"),  # the control port
        ("../x", [9001], "0.0.0.0"),
        ("pi-1", [9001], "0.0.0.0"),  # name taken
        ("pi-2", [9001], "10.0.0.1"),
    ):
        with pytest.raises(ProxyError):
            proxy.add_client(name, ports, bind)
    with pytest.raises(ProxyError):
        proxy.set_role("server", 9000)  # a client's port


def test_a_client_config_is_checked_and_keeps_its_token(proxy: Proxy) -> None:
    forwards = [{"remote_port": 8123, "local": "127.0.0.1:8123"}]
    good = {"server": "proxy.example.org:7000", "fingerprint": "AB" * 32, "name": "pi",
            "token": "t" * 64, "forwards": forwards}  # fmt: skip
    proxy.set_client(good)
    proxy.set_client({**good, "token": ""})  # empty keeps the token
    assert proxy.state()["client"]["token"] == "t" * 64
    assert "token" not in proxy.public()["client"]
    for bad in ({"server": "nohost"}, {"fingerprint": "xyz"}, {"name": "a b"}, {"token": "short"},
                {"forwards": [{"remote_port": 80, "local": "127.0.0.1:80"}]},
                {"forwards": [{"remote_port": 8123, "local": "http://x/"}]},
                {"forwards": [{"remote_port": 8123, "local": "a:1"},
                              {"remote_port": 8123, "local": "b:2"}]}):  # fmt: skip
        with pytest.raises(ProxyError):
            proxy.set_client({**good, **bad})


async def test_the_dashboard_needs_the_password_and_shows_a_token_once(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    headers = {"X-CSRF-Token": await _login(client)}
    app.addons.proxy = Proxy(app.settings.data_dir, app.db)
    try:
        role = {"role": "server", "listen_port": 7100}
        wrong = {**role, "password": "x"}
        refused = await client.put("/api/proxy/role", json=wrong, headers=headers)
        assert refused.status == 403
        ok = await client.put("/api/proxy/role", json={**role, "password": PASSWORD},
                              headers=headers)  # fmt: skip
        assert ok.status == 200
        body = {"name": "ubuntu", "ports": [8080], "host": "home.example.org", "password": PASSWORD}
        made = await (await client.post("/api/proxy/clients", json=body, headers=headers)).json()
        assert made["config"]["server"] == "home.example.org:7100"
        assert made["config"]["token"] == made["token"]
        shown = await (await client.get("/api/proxy", headers=headers)).json()
        assert made["token"] not in json.dumps(shown)
        assert shown["clients"][0]["name"] == "ubuntu"
        topology = await (await client.get("/api/topology", headers=headers)).json()
        assert topology["proxy"]["peers"][0]["name"] == "ubuntu"
        gone = await client.delete("/api/proxy/clients/ubuntu", headers=headers)
        assert gone.status == 200
        assert (await client.delete("/api/proxy/clients/ubuntu", headers=headers)).status == 404
    finally:
        await client.close()
        app.db.close()
