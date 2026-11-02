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

Mit dem VPN erreichen Handys und Laptops von überall Ihr Zuhause – Dashboard,
Kameras und den Werbeblocker von Pi-hole –, ohne dass etwas außer einem
UDP-Port ins Internet geöffnet wird.

```bash
sudo ./scripts/wireguard.sh setup --endpoint meinname.myfritz.net   # einmalig
```

Am besten ist ein Name, der Ihrer öffentlichen IP-Adresse folgt (MyFRITZ!:
*Internet › MyFRITZ!-Konto*, oder DynDNS). Ohne `--endpoint` wird die aktuelle
öffentliche IP verwendet; ändert sie sich, muss jedes Gerät den QR-Code neu
scannen. Später ändern: `sudo ./scripts/wireguard.sh endpoint <name>` oder in
den Dashboard-Einstellungen.

Danach in der FRITZ!Box **UDP 51820** an den Pi freigeben (*Internet ›
Freigaben › Portfreigaben › Gerät für Freigaben hinzufügen*).

### Geräte verwalten

Dashboard › **Netzwerk › VPN-Zugang › Gerät hinzufügen**: Namen eingeben
(„Handy Anna“), wählen, ob der gesamte Internetverkehr über Zuhause läuft
(Werbeblocker überall, sicherer in fremden WLANs) oder nur das Heimnetz, und
den QR-Code mit der App „WireGuard“ scannen. Der QR-Code lässt sich später
erneut anzeigen; für Laptops gibt es eine `.conf`-Datei. Entfernte Geräte sind
nach wenigen Sekunden ausgesperrt.

Technische Details: [network.md](../network.md#wireguard-home-vpn).
