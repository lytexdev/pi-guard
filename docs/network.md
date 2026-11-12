# Network: Pi-hole, WireGuard, fixed address

All of this is optional. pi-guard does not install or change network services
by itself; the installer only offers each step and asks first.

## In the dashboard

**Network › Overview** starts with the state at a glance: devices online, devices without a name, how much Pi-hole
blocks, the Wi-Fi networks and connected VPN devices; each leads to its details. Below are the name lookups of the
last 24 hours per hour (allowed and blocked, also as a table), devices without a name to give one, the busiest devices
of the day, Pi-hole with pause buttons, the Wi-Fi networks with QR codes, and quick ways to block a site, Kid Protect,
the guest Wi-Fi, the network map, what others can see, and moving.

**Network › VPN** shows on top whether the VPN runs, how many devices are connected, the way from outside and whether
the quantum-safe key exchange is on. Below are its parts: *Devices* (cards with state and traffic, *Access* shows the
QR code again, *Add device* opens a short form), *From outside*, *Privacy* and *Quantum-safe*. Each device shows what it
is (Android, iPhone, Windows, Linux, Mac, tablet, computer): devices paired with the app say so themselves, others take
the type chosen in the form or in the device's details, or one guessed from their name. Under *Privacy*, the multi-hop
chain shows each hop, and the (i) next to *Strict DNS privacy* explains what it changes.

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

## Block lists

Dashboard › Network › Block lists manages Pi-hole's lists: recommended ones with a click,
any list by link, or your own `.txt` file (hosts format, one domain per line, or `||domain^`).
Links are downloaded by Pi-hole when you press *Update lists*; the domains of a file are
blocked individually, up to 50,000.

## Device details

The magnifier next to a device pings it, tries common ports once and reads the title of a web
page, and estimates kind and operating system. This only works for private addresses and
only when you ask for it. It is an estimate: firewalls hide ports, and phones with a private
Wi-Fi address change their MAC.

## WireGuard (home VPN)

With the VPN, phones and laptops reach home from anywhere — dashboard, cameras,
Pi-hole ad blocking — without opening anything but one UDP port.

```bash
sudo ./scripts/wireguard.sh setup --endpoint myname.myfritz.net   # once
```

Use a name that follows your public IP address (MyFRITZ!: *Internet ›
MyFRITZ!-Konto*, or any DynDNS). Without `--endpoint` the current public IP is
used; it can change, and every device would need its QR code again. Change it
later with `sudo ./scripts/wireguard.sh endpoint <name>` or in the dashboard
settings.

Then forward **UDP 51820** in the router to the Pi (FRITZ!Box: *Internet ›
Freigaben › Portfreigaben › Gerät für Freigaben hinzufügen*).

### Ways home from outside

Dashboard › **Network › VPN › From outside** shows five ways with their state. Which ones work depends on your
internet connection, not on the router brand. Set up several: the app tries them in turn and
remembers the one that answered.

| Way | Needs | Fits |
| --- | --- | --- |
| **IPv4** | a public IPv4 address, a DynDNS name, UDP 51820 forwarded in the router | most DSL lines |
| **IPv6** | a name with the Pi's own IPv6 address, UDP 51820 allowed in the router, IPv6 on the phone's network | DS-Lite and CGNAT with IPv6 |
| **Rented server** | a small Debian or Ubuntu server | every connection, also LTE and Starlink |
| **Tailscale** | a Tailscale account, the Tailscale app on the phone | every connection, no router change |
| **Disguised (AmneziaWG)** | the IPv4 or IPv6 way, plus a second UDP port forwarded | networks that block WireGuard: hotels, offices, some countries |

Many cable and fibre lines have no IPv4 address of their own (DS-Lite, CGNAT), and port forwarding
then does nothing. If the router's WAN address differs from what the internet sees, or starts with
`100.64`–`100.127`, this applies to you. *Check now* compares what it can. From inside, pi-guard
cannot see whether a router really lets packets through: a way counts as confirmed only when a
device connected over it from outside.

**IPv4:** `sudo ./scripts/wireguard.sh endpoint name.example.org`. Any router works, the menu is
usually called port forwarding or port sharing. The name must follow your public address: the
router's DynDNS or a service like DuckDNS. pi-guard does not update the name itself.

**IPv6:** `sudo ./scripts/wireguard.sh endpoint6 pi.name.example.org`. The name must lead to the Pi
itself, not to the router, and follow its address when the prefix changes. FRITZ!Box gives every
shared device its own MyFRITZ! name. Open UDP 51820 for the Pi over IPv6.

**Disguised (AmneziaWG):** `sudo ./scripts/amneziawg.sh install` (UDP 443 unless you name a port), then
forward that UDP port to the Pi like the WireGuard port, and restart pi-guard. See [vpn.md](vpn.md#disguised-amneziawg).

**Rented server:**

```bash
sudo ./scripts/wireguard.sh setup --no-endpoint   # only if WireGuard is not set up yet
sudo ./scripts/relay.sh setup root@vps.example.org
```

You need ssh access as root and a Debian or Ubuntu server. The Pi dials out, so nothing changes at
home. The server passes the VPN on: it cannot read it, because the encryption runs from the phone
to the Pi. It also gets nothing else. Of everything arriving through its tunnel the Pi accepts only
the VPN port and forwards nothing, so a broken-into server does not reach your network.
Only IPv4 is passed on, and the double packaging costs a little speed.
`sudo ./scripts/relay.sh remove root@vps.example.org` takes it away from both sides.

With `--domain guard.example.org` the server also serves the dashboard at that address through
Caddy, with a real certificate. **There the server does see your traffic, including passwords**, as
any reverse proxy does. Use a server you trust and read [public-access.md](public-access.md). The
VPN alone needs no such trust.

**Tailscale:** `sudo ./scripts/tailscale.sh setup`, then the Tailscale app on the phone with the
same account. Android runs one VPN at a time, so the VPN inside PiGuard stays off, and the PiGuard
app finds the dashboard at its Tailscale address by itself. Open the app once at home first.
Tailscale is a company's service in between; its connections stay end-to-end encrypted, but your
devices are listed there.

### Devices

Dashboard › **Network › VPN › Devices › Add device**: enter a name ("Anna's
phone"), choose whether all internet traffic goes through home (ad blocking
and safety in public Wi-Fi) or only the home network, and scan the QR code with
the WireGuard app (iOS/Android). The QR code can be shown again later; laptops
download a `.conf` file. Removing a device locks it out within seconds.

### How it works

pi-guard runs without root rights and never changes the network itself:

| Part | Runs as | Does |
| --- | --- | --- |
| `scripts/wireguard.sh setup` | root, once | server key in `/etc/wireguard/wg0.base`, IP forwarding, NAT for 10.8.0.0/24, ufw rules, systemd units |
| dashboard | pi-guard | creates device keys (private keys stored AES-GCM encrypted with the dashboard secret key), writes public key, preshared key and address per device to `/var/lib/pi-guard/wireguard/peers` |
| `pi-guard-wg-apply` (path unit) | root | accepts only lines of the form `key<TAB>key<TAB>10.8.0.N/32`, writes `wg0.conf`, `wg syncconf` |
| `pi-guard-wg-status` (timer) | root | status for the dashboard (no keys) every 30 s, in `/run/pi-guard-status`, which pi-guard can only read |

The VPN uses 10.8.0.0/24; the Pi is 10.8.0.1 and the DNS server of the devices
when Pi-hole is enabled. If `PI_GUARD_DASHBOARD_SECRET_KEY` changes, stored
devices keep working but their QR codes cannot be shown again.

FRITZ!OS 7.50+ also has WireGuard built in, an alternative without the Pi —
but without Pi-hole as DNS and without management in the dashboard.

### Privacy upstream

Optional multi-hop exit on the Pi (Mullvad, custom WireGuard, Tor). Devices still
use the home VPN; the Pi forwards selected traffic through an upstream hop.
Install with `sudo ./scripts/privacy-server.sh install`. Details: [vpn.md](vpn.md#privacy-upstream-optional).
