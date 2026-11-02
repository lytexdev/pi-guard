# Network: Pi-hole, WireGuard, fixed address

All of this is optional. pi-guard does not install or change network services
by itself; the installer only offers each step and asks first.

## Fixed IP address

A device that serves DNS (Pi-hole) or VPN needs an address that does not
change. Two ways:

1. **Reserve it in the router** (recommended, nothing changes on the Pi).
   FRITZ!Box: *Heimnetz › Netzwerk › (device) › Diesem Netzwerkgerät immer die
   gleiche IPv4-Adresse zuweisen*.
2. **Set it on the Pi** (Raspberry Pi OS Bookworm, NetworkManager):

   ```bash
   sudo ./scripts/network.sh show
   sudo ./scripts/network.sh static                     # keeps the current address
   sudo ./scripts/network.sh static --ip 192.168.178.10/24 --gateway 192.168.178.1 --dns 127.0.0.1
   sudo ./scripts/network.sh dhcp                       # back to automatic
   ```

   Before switching, the script schedules a rollback for two minutes later and
   only cancels it if you can still confirm afterwards. If the SSH connection
   drops, the old settings come back by themselves. Pick an address outside
   the router's DHCP range.

   Older systems with `dhcpcd` (Bullseye): add to `/etc/dhcpcd.conf`

   ```
   interface wlan0
   static ip_address=192.168.178.10/24
   static routers=192.168.178.1
   static domain_name_servers=127.0.0.1
   ```

## Pi-hole (network-wide ad blocking and DNS)

### Install

- **Natively** (recommended on a Pi): the official installer,
  `curl -sSL https://install.pi-hole.net | bash`. `scripts/install.sh` offers
  to run it. Pi-hole's own web interface stays on ports 80/443; the pi-guard
  dashboard uses 8443.
- **Docker:** `docker compose --profile pihole up -d`; set `PIHOLE_PASSWORD`
  in `.env`. Pi-hole's web interface is then on port 8080.

Then make the router hand out the Pi's address as DNS server (FRITZ!Box:
*Heimnetz › Netzwerk › Netzwerkeinstellungen › IPv4-Einstellungen › Lokaler
DNS-Server*). Only then do devices use Pi-hole.

### Connect pi-guard

In Pi-hole: *Settings › Web interface / API › Configure app password*. Then:

```dotenv
PI_GUARD_PIHOLE_ENABLED=true
PI_GUARD_PIHOLE_URL=http://127.0.0.1          # Docker: http://pihole
PI_GUARD_PIHOLE_PASSWORD=<app password>
```

pi-guard uses the Pi-hole v6 API (v5 is not supported) and shows in the
dashboard: blocked share, number of domains on block lists, active clients,
the most blocked domains, and buttons to pause blocking for 5 minutes or an
hour (it switches back on automatically).

### Pi-hole as presence source

Phones keep making DNS queries in the background. Add `pihole` to the
presence sources to count a trusted device as present when it queried Pi-hole
in the last five minutes:

```yaml
presence:
  sources: [neighbours, pihole]
```

## Devices in the network

The dashboard's *Network* page lists the devices the Pi already knows about:
its neighbour (ARP/NDP) table and, if connected, Pi-hole's client list with
names and manufacturers. **Nothing is scanned.** Devices with an `R` use a
private (random) MAC address. *Use as trusted device* adds a device to
`presence.devices`.

## WireGuard (home VPN)

With a VPN you can reach the dashboard from anywhere without opening it to the
internet. Recommended: [PiVPN](https://pivpn.io) with WireGuard.

```bash
curl -L https://install.pivpn.io | bash     # choose WireGuard; uses Pi-hole as DNS if present
sudo pivpn add -n phone                      # one profile per device
sudo pivpn -qr phone                         # QR code for the WireGuard app
```

Forward UDP port 51820 in the router to the Pi (FRITZ!Box: *Internet ›
Freigaben*). FRITZ!Box 7.50+ also has WireGuard built in, which is an
alternative to running it on the Pi.

### Status in the dashboard

Reading WireGuard's state needs root rights, which pi-guard does not have. A
small root job writes the status (peer names, endpoints, last handshake,
traffic — never private or preshared keys) every 30 seconds to
`/run/pi-guard/wireguard.status`:

```bash
sudo install -m 0755 deploy/wireguard/pi-guard-wg-status /usr/local/sbin/
sudo install -m 0644 deploy/wireguard/pi-guard-wg-status.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now pi-guard-wg-status.timer
```

and set `PI_GUARD_WIREGUARD_ENABLED=true`. The installer does this when it
finds WireGuard. Adding devices stays a root task (`pivpn add`); the dashboard
shows the command.
