# Reverse-Proxy (pgproxy)

Erreiche einen Dienst hinter NAT, etwa Home Assistant zu Hause oder ein NAS, über einen Server mit öffentlicher
Adresse, wie mit [frp](https://github.com/fatedier/frp), aber klein und für pi-guard in Rust geschrieben (`proxy/`).
Englisch: [../proxy.md](../proxy.md).

## So funktioniert es

Ein **Client** verbindet sich über TLS 1.3 nach außen mit dem **Server**, beim Client muss also nichts geöffnet werden.
Für jeden Besucher auf einem seiner öffentlichen Ports bittet der Server den Client um eine neue Verbindung und verbindet
die beiden.

- **Der Server entscheidet.** Jeder Client hat einen Namen, ein Token und die Ports, die er öffnen darf (ab 1024). Fragt
  ein Client nach einem anderen Port, bekommt er ein Nein, und jeder Port gehört nur einem Client.
- **Tokens** erzeugt der Server, zeigt sie einmal und behält nur ihren SHA-256. Ein falsches Token kostet eine Sekunde,
  und zehn Fehlversuche in einer Minute sperren diese Adresse für den Rest der Minute.
- **Festes Zertifikat.** Der Server erzeugt sein eigenes Zertifikat. Clients vertrauen genau diesem, über seinen
  SHA-256-Fingerabdruck; niemand dazwischen kann sich als Server ausgeben, auch ohne Zertifizierungsstelle.
- **Der Client bestimmt das Ziel.** Der Server nennt nur einen Port; der Client leitet ihn an das Ziel aus seiner eigenen
  Konfiguration weiter. Ein Server kann einen Client nie woandershin verbinden lassen.
- **Eine Statusdatei** fürs Dashboard, mit verbundenen Clients, Ports, Verbindungen und Bytes, nie Tokens. Bytes zählen,
  wenn eine Verbindung endet.
- Nur TCP. Jeder Besucher bekommt seine eigene TLS-Verbindung, es gibt kein Multiplexing.

## Aufbauten

| Beispiel | Server | Clients |
|---|---|---|
| Dienste zu Hause über einen gemieteten Ubuntu-Server | Ubuntu | Arch, Pi, Pi |
| Dein Rechner als Mittelpunkt | Arch | Pi, Pi |
| Der Pi als Mittelpunkt | Pi | Arch |

Jede Mischung geht: Jeder Rechner läuft als `pgproxy server` oder `pgproxy client`.

## Auf dem Pi

*Add-ons → Reverse-Proxy*. Wähle, ob dieser Pi Server oder Client ist; Änderungen brauchen dein Passwort.

- **Server:** Steuer-Port eintragen, dann *Rechner hinzufügen* mit Name und Ports. Das Dashboard zeigt die Konfiguration
  für diesen Rechner einmal, mit Token: Speichere sie dort als `client.json` und ersetze `PORT` durch den lokalen Port
  des jeweiligen Dienstes. *Ports nur auf diesem Server* hält sie auf 127.0.0.1 des Servers, für dessen eigenen
  Reverse-Proxy wie Caddy.
- **Client:** Server, Fingerabdruck, Name und Token eintragen, dazu die Weiterleitungen: ein Port auf dem Server und ein
  Dienst hier als `Host:Port`. Alles Weitergeleitete ist vom Server aus erreichbar, vielleicht aus dem Internet; das
  Dashboard nur mit starker Anmeldung weiterleiten.
- pi-guard startet und überwacht `pgproxy` selbst. Die Karte unter *Netzwerk* zeigt die Clients des Proxys oder seinen
  Server.
- An Router und Firewall wird nichts geändert. Auf einem Server öffnest du Steuer-Port und Client-Ports dort selbst.

pgproxy muss auf dem Pi liegen: `cd proxy && cargo build --release` auf einem Rechner mit C-Compiler für den Pi (siehe
[vpn.md](../vpn.md) zum Bauen für andere Prozessoren), oder nimm `pgproxy-linux-aarch64` aus einem Release und prüfe es
gegen `SHA256SUMS`. Dann `sudo ./scripts/install-proxy.sh pfad/zu/pgproxy`.

## Auf anderen Rechnern

```bash
cd proxy && cargo build --release && sudo ../scripts/install-proxy.sh

# ein Server
sudo pgproxy init /etc/pgproxy          # zeigt den Fingerabdruck für die Clients
pgproxy token                           # ein Token und der Hash für den Server
sudoedit /etc/pgproxy/server.json
sudo ./scripts/install-proxy.sh service server

# ein Client: die client.json aus dem Dashboard oder von Hand
sudoedit /etc/pgproxy/client.json
sudo ./scripts/install-proxy.sh service client
```

Aufbau von `server.json` und `client.json`: siehe [../proxy.md](../proxy.md#on-other-machines). `pgproxy check
server|client DATEI` prüft eine Datei, ohne etwas zu starten; unbekannte Felder werden abgelehnt. Der Dienst läuft als
eigener Nutzer `pgproxy` ohne besondere Rechte, und `/etc/pgproxy` können nur root und dieser Nutzer lesen.

## Was getestet ist

Unit- und Ende-zu-Ende-Tests auf einem Rechner: Ein Besucher erreicht einen Dienst über Server und Client; falsche
Tokens, nicht erlaubte Ports und Server mit anderem Zertifikat werden abgelehnt; eine vom Dashboard des Pi erzeugte
Konfiguration läuft mit dem echten `pgproxy`. Noch nicht getestet: ein Pi mit installiertem pgproxy und Rechner in
verschiedenen Netzen.
