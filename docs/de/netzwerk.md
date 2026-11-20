# Netzwerk: Pi-hole, WireGuard, feste Adresse

Alles hier ist optional. Das Installationsprogramm fragt vor jedem Schritt.

## Im Dashboard

**Netzwerk › Überblick** zeigt oben den Zustand auf einen Blick: Geräte online, Geräte ohne Namen, wie viel Pi-hole
blockt, die WLANs und verbundene VPN-Geräte; alles führt zu den Details. Darunter die Namensanfragen der letzten 24
Stunden pro Stunde (erlaubt und blockiert, auch als Tabelle), Geräte ohne Namen zum Benennen, die aktivsten Geräte des
Tages, Pi-hole mit Pause, die WLANs mit QR-Codes und schnelle Wege zu Seite sperren, Kid Protect, Gäste-WLAN,
Netzwerkkarte, was andere sehen, und Umzug.

**Netzwerk › VPN** zeigt zuerst, ob das Heim-VPN läuft und wie viele Geräte verbunden sind, danach optionale
**Schichten** als Karten: Heim-VPN (Basis), quantenresistente Schlüssel, AmneziaWG-Tarnung und Mullvad / Multi-Hop.
Jede Schicht ist wahlfrei; keine, eine oder mehrere. Die AmneziaWG-Karte wirkt auf dem Pi selbst: Aus nimmt `awg0` herunter, und die Apps bekommen
den getarnten Weg nicht mehr; An bringt beides zurück. Darunter die Bereiche *Geräte*, *Unterwegs / Amnezia*,
*Mullvad / Hop* und *Quanten*. Geräte-Karten zeigen Zustand und Datenmenge; *Zugang* zeigt den QR-Code erneut;
*Gerät hinzufügen* öffnet ein kurzes Formular. In den Details eines Geräts kannst du es umbenennen, den
**Internetweg** wählen (nur Heimanschluss, wie der Pi, oder über Mullvad / Multi-Hop) und den Verkehr des letzten
Tages sehen. Jedes Gerät zeigt, was es ist (Android, iPhone, Windows, Linux, Mac, Tablet, Computer): Mit der App
verbundene Geräte sagen es selbst, andere nehmen den gewählten Typ oder einen aus dem Namen geratenen. Unter
*Mullvad / Hop* zeigt die Multi-Hop-Kette jeden Hop; Details: [vpn.md](../vpn.md).

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

Unter **Netzwerk › VPN › Geräte**: Gerät tippen → umbenennen, Typ wählen, Internetweg (drei klare Optionen) und
Nutzung der letzten Stunden. *Zugang* zeigt QR-Code und Konfigurationsdatei erneut.

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

**Ein anderes VPN statt WireGuard von pi-guard:** Läuft Tailscale, ZeroTier oder NetBird auf dem Pi und ist das
WireGuard von pi-guard aus, sagt *Netzwerk → VPN* das. Die WireGuard-Wege, AmneziaWG und die Quanten-Schlüssel wirken
nur mit dem WireGuard von pi-guard und stehen dann auf *nicht nötig*. Läuft AmneziaWG noch, bietet die Seite an, es
auszuschalten.

Details: [network.md](../network.md#ways-home-from-outside).

Dashboard › **Netzwerk › VPN › Geräte › Gerät hinzufügen**: Namen eingeben
(„Handy Anna“), wählen, ob der gesamte Internetverkehr über Zuhause läuft
(Werbeblocker überall, sicherer in fremden WLANs) oder nur das Heimnetz, und
den QR-Code mit der App „WireGuard“ scannen. Der QR-Code lässt sich später
erneut anzeigen; für Laptops gibt es eine `.conf`-Datei. Entfernte Geräte sind
nach wenigen Sekunden ausgesperrt.

Technische Details: [network.md](../network.md#wireguard-home-vpn).

### Ein Name, der deiner Adresse folgt (Cloudflare-DDNS)

Mit einer Domain bei Cloudflare hält pi-guard einen Namen wie `home.example.org` selbst auf deinem Zuhause:
**Netzwerk › VPN › Dynamisches DNS (Cloudflare)**. Bei Cloudflare einen API-Token erstellen (*Mein Profil → API-Tokens →
Token erstellen*, Vorlage *DNS-Zone bearbeiten*, nur für diese Zone), Name und Token eintragen, einschalten und
speichern. Alle fünf Minuten fragt der Pi seine öffentliche IPv4-Adresse ab (api.ipify.org) und setzt bei einer Änderung
den A-Eintrag; mit *Auch IPv6* zusätzlich den AAAA-Eintrag auf die eigene IPv6-Adresse des Pi. Einträge laufen nie über
Cloudflares Proxy (orange Wolke aus): WireGuard ist UDP und braucht die echte Adresse. *Jetzt aktualisieren* probiert den
Token sofort aus.

Hat das VPN keine eigene Adresse (`PI_GUARD_WIREGUARD_ENDPOINT`), nimmt es diesen Namen, ebenso die verbundenen
Haushalte; *Stattdessen den DDNS-Namen nehmen* ersetzt eine ältere Adresse wie eine feste IP. Im Router muss UDP 51820
(und der Port der Haushalte) weiter weitergeleitet sein. Der Token wird wie jedes Geheimnis gespeichert, nie wieder
angezeigt und nie protokolliert. *System → Zustand* zeigt, ob die letzte Aktualisierung geklappt hat. Getestet gegen eine
nachgebaute Cloudflare-API, noch nicht gegen eine echte Zone.

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
nicht. Signierte Updates halten Sync- und Privacy-Units aktuell; `vpn-server.sh install` nur, wenn sich die
VPN-Server-Binary selbst geändert hat.

### Privacy-Upstream

Optionaler Ausgang auf dem Pi: Mullvad (auf Wunsch Multi-Hop) oder ein eigener WireGuard-Server, mit einfachen
Schaltern: Mullvad, Multi-Hop, Tarnung (AmneziaWG) und Kill-Switch, jeweils an oder aus. Geräte nutzen weiter das
Heim-VPN; pro Gerät gibt es zwei Schalter, *Über Mullvad* und *Tarnung*. Tor gibt es seit 1.14 nicht mehr. Installation:
`sudo ./scripts/privacy-server.sh install`. Details: [vpn.md](../vpn.md#privacy-upstream-optional).

Mullvad: unter *Netzwerk › VPN › Privatsphäre* verknüpft der Schalter *Mullvad* das Konto mit seiner 16-stelligen
Nummer (ein Konto ohne bezahlte Zeit wird abgelehnt) und fragt nach Land, Stadt und Server für den Ausgang, mit
*Multi-Hop* auch nach einem Eingang. Es gibt immer nur einen Ausgang; ein neuer ersetzt den alten. Schlüssel, Adressen und Ports nimmt der Pi aus Mullvads Serverliste. Bis 2026-11-12 kam kein Exit hoch
(`invalid exit.peer` im Journal), mit Kill-Switch hatten VPN-Geräte dann kein Internet. Signierte Updates
bringen geänderte Exit-Units von allein mit; `privacy-server.sh install` nur einmal zur Erstinstallation.
Eine echte Verbindung zu Mullvad ist noch nicht getestet.
