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

### Devices

Dashboard › **Network › VPN access › Add device**: enter a name ("Anna's
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
| `pi-guard-wg-status` (timer) | root | status for the dashboard (no keys) every 30 s |

The VPN uses 10.8.0.0/24; the Pi is 10.8.0.1 and the DNS server of the devices
when Pi-hole is enabled. If `PI_GUARD_DASHBOARD_SECRET_KEY` changes, stored
devices keep working but their QR codes cannot be shown again.

FRITZ!OS 7.50+ also has WireGuard built in, an alternative without the Pi —
but without Pi-hole as DNS and without management in the dashboard.
