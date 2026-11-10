# Reverse proxy (pgproxy)

Reach a service behind NAT, such as Home Assistant at home or a NAS, through a server with a public address, like
[frp](https://github.com/fatedier/frp), but small and written for pi-guard in Rust (`proxy/`). German:
[de/proxy.md](de/proxy.md).

## How it works

A **client** connects out to the **server** over TLS 1.3, so nothing needs to be opened at the client's side. For
each visitor on one of its public ports, the server asks the client for a new connection and joins the two.

- **The server decides.** Each client has a name, a token and the ports it may open (1024 and up). A client asking for
  any other port is told no, and a port is given to one client only.
- **Tokens** are made by the server, shown once, and kept there only as SHA-256. A wrong token costs a second, and ten
  wrong tries in a minute block that address for the rest of the minute.
- **Pinned certificate.** The server makes its own certificate. Clients trust exactly that one, by its SHA-256
  fingerprint, so a man in the middle cannot pose as the server, also without a certificate authority.
- **The client decides where traffic goes.** The server only names a port; the client forwards it to the target set
  in its own configuration. A server can never make a client connect anywhere else.
- **A status file** for the dashboard, with connected clients, ports, connections and bytes, never tokens. Bytes count
  when a connection ends.
- TCP only. Each visitor gets its own TLS connection, there is no multiplexing.

## Topologies

| Example | Server | Clients |
|---|---|---|
| Home services on a rented Ubuntu server | Ubuntu | Arch, Pi, Pi |
| Your desktop as the hub | Arch | Pi, Pi |
| The Pi as the hub | Pi | Arch |

Any mix works: every machine runs `pgproxy server` or `pgproxy client`.

## On the Pi

*Add-ons → Reverse proxy*. Choose whether this Pi is server or client; changes need your password.

- **Server:** set the control port, then *Add a machine* with a name and its ports. The dashboard shows the
  configuration for that machine once, token included: save it there as `client.json` and replace `PORT` with the
  local port of each service. *Ports only on this server* keeps them on 127.0.0.1 of the server, for its own reverse
  proxy such as Caddy.
- **Client:** enter server, fingerprint, name and token, and what to forward: a port on the server and a service here
  as `host:port`. Everything forwarded is reachable from the server, maybe from the internet; forward the dashboard
  only with a strong login.
- pi-guard starts and watches `pgproxy` itself. The *Network* map shows the proxy's clients or its server.
- Nothing changes your router or firewall. On a server, open the control port and the client ports there yourself.

pgproxy has to be on the Pi: `cd proxy && cargo build --release` on a computer with a C compiler for the Pi (see
[vpn.md](vpn.md) for building for other processors), or take `pgproxy-linux-aarch64` from a release and check it
against `SHA256SUMS`. Then `sudo ./scripts/install-proxy.sh path/to/pgproxy`.

## On other machines

```bash
cd proxy && cargo build --release && sudo ../scripts/install-proxy.sh

# a server
sudo pgproxy init /etc/pgproxy          # prints the fingerprint for the clients
pgproxy token                           # a token, and the hash for the server
sudoedit /etc/pgproxy/server.json
sudo ./scripts/install-proxy.sh service server

# a client: the client.json from the dashboard, or written by hand
sudoedit /etc/pgproxy/client.json
sudo ./scripts/install-proxy.sh service client
```

`server.json`:

```json
{
  "listen": "0.0.0.0:7000",
  "cert": "/etc/pgproxy/cert.pem",
  "key": "/etc/pgproxy/key.pem",
  "status": "/var/lib/pgproxy/status.json",
  "clients": [
    {"name": "arch", "token_sha256": "…", "ports": [{"port": 8080, "bind": "0.0.0.0"}]}
  ]
}
```

`client.json`:

```json
{
  "server": "proxy.example.org:7000",
  "fingerprint": "AB:CD:…",
  "name": "arch",
  "token": "…",
  "forwards": [{"remote_port": 8080, "local": "127.0.0.1:80"}]
}
```

`pgproxy check server|client FILE` checks a file without starting anything; unknown fields are refused. The service
runs as its own `pgproxy` user without any capabilities, and `/etc/pgproxy` is readable only by root and that user.

## What was tested

Unit and end-to-end tests on one computer: a visitor reaches a service through server and client; wrong tokens,
ports the server did not allow and servers with another certificate are refused; a configuration made by the Pi's
dashboard runs with the real `pgproxy`. Not tested yet: a Pi with pgproxy installed, and machines in different
networks.
