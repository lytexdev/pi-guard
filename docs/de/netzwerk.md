# Netzwerk: Pi-hole, WireGuard, feste Adresse

Alles hier ist optional. Das Installationsprogramm fragt vor jedem Schritt.

## Im Dashboard

**Netzwerk › Überblick** zeigt oben den Zustand auf einen Blick: Geräte online, Geräte ohne Namen, wie viel Pi-hole
blockt, die WLANs und verbundene VPN-Geräte; alles führt zu den Details. Darunter die Namensanfragen der letzten 24
Stunden pro Stunde (erlaubt und blockiert, auch als Tabelle), Geräte ohne Namen zum Benennen, die aktivsten Geräte des
Tages, Pi-hole mit Pause, die WLANs mit QR-Codes und schnelle Wege zu Seite sperren, Kid Protect, Gäste-WLAN,
Netzwerkkarte, was andere sehen, und Umzug.

**Netzwerk › VPN** zeigt oben, ob das VPN läuft, wie viele Geräte verbunden sind, den Weg von unterwegs und ob der
quantensichere Schlüsseltausch an ist. Darunter die Bereiche *Geräte* (Karten mit Zustand und Datenmenge, *Zugang* zeigt
den QR-Code erneut, *Gerät hinzufügen* öffnet ein kurzes Formular), *Von unterwegs*, *Privatsphäre* und *Quantensicher*.
Jedes Gerät zeigt, was es ist (Android, iPhone, Windows, Linux, Mac, Tablet, Computer): Mit der App verbundene Geräte
sagen es selbst, andere nehmen den im Formular oder in den Details gewählten Typ oder einen aus dem Namen geratenen.
Unter *Privatsphäre* zeigt die Multi-Hop-Kette jeden Hop, und das (i) neben *Strikte DNS-Privatsphäre* erklärt, was es
ändert.

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

### Wege von unterwegs

Im Dashboard unter **Netzwerk › VPN › Von unterwegs** stehen fünf Wege mit ihrem Zustand. Was funktioniert, hängt
vom Internetanschluss ab, nicht von der Router-Marke. Richten Sie gern mehrere ein: Die App probiert
sie der Reihe nach und merkt sich den, der geantwortet hat.

| Weg | Braucht | Passt zu |
| --- | --- | --- |
| **IPv4** | eigene öffentliche IPv4-Adresse, DynDNS-Name, UDP 51820 im Router freigegeben | den meisten DSL-Anschlüssen |
| **IPv6** | Namen mit der IPv6-Adresse des Pi, UDP 51820 im Router erlaubt, IPv6 im Handynetz | DS-Lite und CGNAT mit IPv6 |
| **Mietserver** | kleinen Debian- oder Ubuntu-Server | jedem Anschluss, auch LTE und Starlink |
| **Tailscale** | Tailscale-Konto, Tailscale-App auf dem Handy | jedem Anschluss, ohne Router-Änderung |
| **Getarnt (AmneziaWG)** | den IPv4- oder IPv6-Weg und einen zweiten weitergeleiteten UDP-Port | Netzen, die WireGuard sperren: Hotel, Firma, manche Länder |

Viele Kabel- und Glasfaseranschlüsse (zum Beispiel oft bei Vodafone) haben keine eigene IPv4-Adresse
(DS-Lite, CGNAT). Eine Portfreigabe bewirkt dann nichts. Das trifft Sie, wenn die WAN-Adresse im
Router von der abweicht, die das Internet sieht, oder mit `100.64` bis `100.127` beginnt. *Jetzt
prüfen* vergleicht, was von innen möglich ist. Ob ein Router wirklich etwas durchlässt, sieht
pi-guard nicht: Als bestätigt gilt ein Weg erst, wenn ein Gerät von außen darüber verbunden war.

```bash
sudo ./scripts/wireguard.sh endpoint name.example.org        # IPv4
sudo ./scripts/wireguard.sh endpoint6 pi.name.example.org    # IPv6, Name führt zum Pi selbst
sudo ./scripts/relay.sh setup root@vps.example.org           # Mietserver
sudo ./scripts/tailscale.sh setup                            # Tailscale
```

**Mietserver:** Der Pi wählt sich selbst ein, zu Hause ändert sich nichts. Der Server kann das VPN
nicht mitlesen, weil die Verschlüsselung vom Handy bis zum Pi reicht. Der Pi nimmt von ihm nur den
VPN-Port an und leitet nichts weiter, ein gehackter Server erreicht also Ihr Heimnetz nicht. Mit
`--domain guard.example.org` zeigt er zusätzlich das Dashboard unter dieser Adresse. **Dabei sieht
der Server Ihren Verkehr samt Passwörtern**, wie jeder Reverse-Proxy. Das VPN allein braucht dieses
Vertrauen nicht.

**Tailscale:** Android erlaubt nur ein VPN gleichzeitig. Das VPN in PiGuard bleibt also aus, und die
PiGuard-App findet das Dashboard selbst unter der Tailscale-Adresse. Öffnen Sie die App vorher einmal
zu Hause. Tailscale ist ein Dienst einer Firma: Die Verbindungen sind Ende-zu-Ende verschlüsselt,
Ihre Geräte stehen aber dort in einer Liste.

Details: [network.md](../network.md#ways-home-from-outside).

Dashboard › **Netzwerk › VPN › Geräte › Gerät hinzufügen**: Namen eingeben
(„Handy Anna“), wählen, ob der gesamte Internetverkehr über Zuhause läuft
(Werbeblocker überall, sicherer in fremden WLANs) oder nur das Heimnetz, und
den QR-Code mit der App „WireGuard“ scannen. Der QR-Code lässt sich später
erneut anzeigen; für Laptops gibt es eine `.conf`-Datei. Entfernte Geräte sind
nach wenigen Sekunden ausgesperrt.

Technische Details: [network.md](../network.md#wireguard-home-vpn).

### Getarnt mit AmneziaWG

AmneziaWG ist WireGuard mit anders aussehenden Paketen (Füllpakete, Polster, andere Kopfzeilen). Netze,
die WireGuard an seinem Aussehen erkennen und sperren, lassen es durch. Auf dem Pi:

```bash
sudo ./scripts/amneziawg.sh install        # UDP 443, oder: install 51821
```

Das baut das Kernelmodul per DKMS (nach Kernel-Updates von selbst neu) und die Werkzeuge `awg`/`awg-quick`
aus festen Versionen mit geprüfter Prüfsumme, richtet `awg0` neben `wg0` ein und öffnet den Port in ufw.
Danach am Router den UDP-Port an den Pi weiterleiten und pi-guard neu starten. Geräte, Schlüssel und
Adressen bleiben die von `wg0`: `pi-guard-awg-sync` überträgt sie jede Sekunde und leitet ein Gerät über
den Weg zurück, über den es sich zuletzt gemeldet hat. Die Handy-App ab 1.8 probiert die getarnten Wege,
wenn die normalen nicht antworten, und zeigt dann *Getarnt (AmneziaWG)*. Die Desktop-App kann es noch
nicht. Nach dem Update auch `sudo ./scripts/vpn-server.sh install` (Post-Quantum-Schlüssel auch über awg0)
und `sudo ./scripts/privacy-server.sh install` (Privacy-Ausgang und Kill-Switch auch für awg0) ausführen.

### Privacy-Upstream

Optionaler Multi-Hop-Exit auf dem Pi (Mullvad, eigener WireGuard, Tor). Geräte nutzen weiter das
Heim-VPN; der Pi leitet ausgewählten Verkehr über einen Upstream-Hop. Installation:
`sudo ./scripts/privacy-server.sh install`. Details: [vpn.md](../vpn.md#privacy-upstream-optional).

Mullvad: unter *Netzwerk › VPN* das Konto mit seiner 16-stelligen Nummer verknüpfen (ein Konto ohne bezahlte Zeit wird
abgelehnt), dann *Server wählen*: Land, Stadt und Server für den Ausgang, und mit *Multi-Hop über einen zweiten Server*
einen Eingang. Schlüssel, Adressen und Ports nimmt der Pi aus Mullvads Serverliste. Bis 2026-11-12 kam kein Exit hoch
(`invalid exit.peer` im Journal), mit Kill-Switch hatten VPN-Geräte dann kein Internet. Nach dem Update
`sudo ./scripts/privacy-server.sh install` erneut ausführen. Eine echte Verbindung zu Mullvad ist noch nicht getestet.
