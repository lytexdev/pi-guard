# Handy-App (Android)

PiGuard ist das Dashboard als Android-App, mit dem, was nur ein Handy kann. Sie ist optional: Schalte **Handy-App**
unter *Add-ons* ein und starte neu.

## Installieren

1. Öffne auf dem Handy im Heimnetz *Add-ons → Handy-App* und drücke **App herunterladen**.
2. Öffne PiGuard im Heimnetz. Die App sucht pi-guard im Netzwerk, oder du trägst die Adresse ein.
3. Prüfe den Zertifikats-Fingerabdruck und melde dich an. Das Handy bekommt einen eigenen Schlüssel und bleibt
   angemeldet.

Neue Handys lassen sich nur im Heimnetz oder über das VPN verbinden. Einen VPN-Zugang bekommen nur Admins: Er reicht ins
ganze Heimnetz, und Betrachter dürfen nur zuschauen. Unter *Add-ons → Handy-App* entfernst du ein
Handy; sein Schlüssel gilt dann sofort nicht mehr.

## Was sie kann

- **Dashboard** mit allen Seiten und Einstellungen, von unterwegs über das VPN oder eine öffentliche Adresse.
- **Alarme** kommen über eine eigene verschlüsselte Verbindung zu pi-guard, ohne Google oder Apple dazwischen. Eine
  dauerhafte Benachrichtigung hält sie offen; das kostet etwas Akku.
- **VPN:** Die App ist ein WireGuard-Client mit eigenem Zugang pro Handy, wie eine VPN-App mit Geräteliste. pi-guard
  legt den Zugang beim Koppeln an, du musst also keinen QR-Code scannen. Tippe oben in der App auf das Schild und
  dann auf *VPN verbinden*. Das geht von überall, zeigt Gerät, Adresse, Datenmenge und letzten Kontakt und verbindet
  sich nach einem Neustart wieder. Android erlaubt nur ein VPN gleichzeitig: Verbinden ersetzt die WireGuard-App. Ist
  diese auf „durchgehend aktiv“ gestellt, lehnt Android ab, und die App erklärt, wie du das ausschaltest. PiGuard kann
  auch selbst das durchgehend aktive VPN sein. Sind mehrere [Wege](netzwerk.md#wege-von-unterwegs) eingerichtet, probiert sie diese der Reihe nach und zeigt, welcher gerade
  läuft. Mit Tailscale findet sie das Dashboard selbst unter dieser Adresse. Für den ersten Zugang muss die App einmal zu Hause oder über ein
  anderes VPN geöffnet werden. WireGuard muss auf pi-guard eingerichtet sein.
  Mach das einmal zu Hause. Der gesamte Verkehr läuft dann über dein Zuhause.
- **Handy finden:** unter *Add-ons → Handy-App* oder mit `/finden Name` im Chat. Es klingelt in voller Lautstärke und
  vibriert. Standort, Karte und Details gehen nur raus, wenn der Besitzer *Standort teilen* eingeschaltet hat. Das Handy
  zeigt an, dass jemand es sucht. Die Kartenkacheln holt der Pi von OpenStreetMap, und nur, wenn jemand die Karte öffnet.
- **Standort-Link:** *Standort-Link* fragt das Handy sofort nach seinem Standort, ohne Klingeln, und zeigt einen Link mit
  der Genauigkeit (± Meter) zum Öffnen, Kopieren oder Teilen. Drinnen hat ein Handy manchmal kein GPS; dann kommt nichts
  Neues. Der Link öffnet die Karte, die unter *Karte für Standorte* (`PI_GUARD_MAP_PROVIDER`) gewählt ist: OpenStreetMap,
  Google Maps, Apple Karten, Bing, HERE oder die Karten-App des Geräts. Der Kartenanbieter erfährt den Ort erst, wenn
  jemand den Link öffnet.
- **VPN-Optionen:** Auf dem VPN-Bildschirm ist *Apps am VPN vorbei* das Split-Tunneling, *Automatisch verbinden* verbindet,
  sobald pi-guard nicht direkt erreichbar ist. Kill Switch und Lockdown sind Androids „Durchgehend aktives VPN“ mit
  „Verbindungen ohne VPN blockieren“: Das System selbst sperrt dann alles, was am Tunnel vorbei ginge.
- **Updates:** kommen einmal am Tag im WLAN von deinem pi-guard; der holt neue signierte Versionen selbst von GitHub. Das erste fragt nach einem Tippen, danach aktualisiert
  sich die App ab Android 12 selbst.
- **Name und Symbol:** *App-Einstellungen → App-Name und Symbol* macht aus der App einen Rechner, Notizen, Wetter, Uhr,
  Dateien oder Musik mit passendem Symbol, zum Beispiel um unauffällig zu bleiben. Android legt diese Namen beim Bauen der
  App fest. Für jeden anderen Namen und ein eigenes Bild erstellst du eine Verknüpfung auf dem Startbildschirm. Ganz
  verstecken lässt sich die App nicht: Seit Android 10 steht jede App mit Berechtigungen in der App-Liste, mit dem
  gewählten Aussehen aber unauffällig.
- **Kalender:** Belegte Einträge des Handy-Kalenders zählen als abwesend. Andere Kalender wie Google oder Proton verbindest
  du im Dashboard unter Einstellungen › Zuhause & Abwesend, auch ohne die App.
- **SOS:** Ein Tippen startet 10 Sekunden Countdown, nochmal tippen bricht ab. Danach bekommen alle Notfallkontakte und
  Admins eine Nachricht mit Standort, Modus, Akku und Netz sowie ein aktuelles Bild jeder Kamera. **Das ersetzt nicht die 112.** Es braucht eine Verbindung zu
  pi-guard, und pi-guard muss laufen.
- **Anrufen:** Auf der Live-Seite schickt *Anrufen* dein Mikrofon an den Lautsprecher, während du die Kamera hörst. Das
  geht auch im normalen Browser. Rechne mit ein bis zwei Sekunden Verzögerung.
- **Kalender:** Mit *Abwesend während Kalendereinträgen* gelten belegte Termine der nächsten 14 Tage als abwesend, auch
  wenn ein Handy zu Hause ist. Nur Start und Ende gehen an pi-guard, Titel bleiben auf dem Handy. Ganztägige Termine
  zählen nicht.
- **App-Sperre:** PIN und Fingerabdruck oder Gesicht, nach 30 Sekunden im Hintergrund. Die PIN tippst du auf einem
  Ziffernblock, der nur Punkte zeigt; beim Festlegen gibst du sie zweimal ein.
- **Knöpfe unter Meldungen:** *Alles in Ordnung* bestätigt alle offenen Meldungen, *Alarm abspielen* lässt zu Hause
  30 Sekunden den Alarm laufen. Beides macht pi-guard nur für Admins.

Auf dem Handy sind die Einstellungen des Dashboards eine Liste: Abschnitt antippen, der Pfeil oben führt zurück.

Die App fragt erst nach der Kamera, wenn du im Dashboard Bilder einer vertrauten Person aufnimmst.

## Bauen und signieren

```bash
./scripts/build-android.sh 1.0.0                  # signierte APK
./scripts/build-android.sh 1.0.0 --publish system # zusätzlich auf einen ssh-Host mit pi-guard legen
```

Das läuft auf einem Rechner mit Android-SDK, nicht auf dem Pi. Der erste Lauf erstellt den Signaturschlüssel in
`~/.config/pi-guard/android`. **Sichere ihn.** Android nimmt Updates nur mit demselben Schlüssel an: Ohne ihn muss jedes Handy
die App löschen und neu verbinden. Er darf nie ins Repository.

Neue Versionen lassen sich auch unter *Add-ons → Handy-App* hochladen. Handys schauen einmal am Tag nach und bieten sie an.
Android fragt vor der Installation immer nach, wenn eine App nicht aus einem Store kommt; ganz still geht es also nicht.

## Sicherheit

- Das Anmelde-Token ist mit einem Schlüssel im Android Keystore verschlüsselt. Auf pi-guard liegt nur sein Hash.
- Es ersetzt in der App Passwort und zweiten Faktor: Ein gestohlenes entsperrtes Handy ist also angemeldet. Nutze die
  App-Sperre und entferne verlorene Handys: Das beendet sofort seine Anmeldung und seinen VPN-Zugang.
- Das Zertifikat wird beim ersten Start festgelegt. Was nicht von deinem pi-guard oder einer öffentlichen Stelle kommt, wird
  abgelehnt.

## pi-guard besser machen

Beim Einrichten und später in den App-Einstellungen gibt es zwei Schalter, beide aus, bis du sie einschaltest:
**Absturzberichte** (nur Art des Fehlers und Stellen im Code, nie die Meldung und nichts, was du gesehen oder
eingegeben hast) und **anonyme Nutzungsdaten** (Version der App und von Android). Beides geht nur an deinen eigenen
pi-guard, und der gibt es nur im anonymen Wochenbericht weiter, wenn auch sein Besitzer zugestimmt hat.

## Die App mit dem eigenen pi-guard

Die App funktioniert mit jedem pi-guard. Die offizielle APK aktualisiert sich über die signierten GitHub-Releases,
die dein pi-guard selbst holt. Wer die App selbst baut (`./scripts/build-android.sh VERSION` mit eigenem Schlüssel),
schaltet *App-Updates von GitHub holen* aus, denn Android nimmt keine Updates mit fremdem Schlüssel an, und
veröffentlicht seine Versionen mit `--publish HOST`.
