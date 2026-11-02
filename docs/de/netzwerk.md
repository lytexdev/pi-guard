# Netzwerk: Pi-hole, WireGuard, feste Adresse

Alles hier ist optional. Das Installationsprogramm fragt vor jedem Schritt.

## Feste IP-Adresse

Am einfachsten in der FRITZ!Box: *Heimnetz › Netzwerk › (Gerät) › Diesem
Netzwerkgerät immer die gleiche IPv4-Adresse zuweisen*.

Oder auf dem Pi selbst:

```bash
sudo ./scripts/network.sh static
```

Das Skript zeigt die aktuelle Adresse, fragt nach und stellt die alten
Einstellungen nach zwei Minuten von selbst wieder her, falls die Verbindung
danach nicht mehr bestätigt werden kann.

## Pi-hole (Werbeblocker für das ganze Heimnetz)

1. Installieren: `curl -sSL https://install.pi-hole.net | bash` (das
   Installationsprogramm von pi-guard bietet das an) oder mit Docker
   `docker compose --profile pihole up -d`.
2. In der FRITZ!Box die Adresse des Pi als DNS-Server eintragen: *Heimnetz ›
   Netzwerk › Netzwerkeinstellungen › IPv4-Einstellungen › Lokaler
   DNS-Server*.
3. In Pi-hole ein App-Passwort erstellen (*Settings › Web interface / API*)
   und in `pi-guard.env` eintragen:

   ```
   PI_GUARD_PIHOLE_ENABLED=true
   PI_GUARD_PIHOLE_PASSWORD=<App-Passwort>
   ```

Im Dashboard sehen Sie dann, wie viel blockiert wird, und können den
Werbeblocker für 5 Minuten oder eine Stunde pausieren. Pi-hole kann außerdem
helfen zu erkennen, ob jemand zuhause ist (`presence.sources: [neighbours, pihole]`).

## Geräte im Heimnetz

Die Seite *Netzwerk* zeigt die Geräte, die der Pi ohnehin kennt (und, wenn
verbunden, die Liste von Pi-hole). Es wird nichts gescannt. Mit einem Tipp
wird ein Handy zum „vertrauten Gerät“ für die Anwesenheitserkennung.

## WireGuard (VPN nach Hause)

Damit erreichen Sie das Dashboard von unterwegs, ohne es ins Internet zu
stellen. Empfohlen: PiVPN.

```bash
curl -L https://install.pivpn.io | bash   # WireGuard wählen
sudo pivpn add -n handy
sudo pivpn -qr handy                      # QR-Code in der WireGuard-App scannen
```

In der FRITZ!Box UDP-Port 51820 an den Pi freigeben. Neuere FRITZ!Box-Modelle
(ab FRITZ!OS 7.50) können WireGuard auch selbst.

Den VPN-Status im Dashboard richtet das Installationsprogramm ein, wenn es
WireGuard findet (Details: [network.md](../network.md)).
