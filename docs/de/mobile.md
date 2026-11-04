# Handy-App (Android)

PiGuard ist das Dashboard als Android-App, mit dem, was nur ein Handy kann. Sie ist optional: Schalte **Handy-App**
unter *Add-ons* ein und starte neu.

## Installieren

1. Öffne auf dem Handy im Heimnetz *Add-ons → Handy-App* und drücke **App herunterladen**.
2. Öffne PiGuard im Heimnetz. Die App sucht pi-guard im Netzwerk, oder du trägst die Adresse ein.
3. Prüfe den Zertifikats-Fingerabdruck und melde dich an. Das Handy bekommt einen eigenen Schlüssel und bleibt
   angemeldet.

Neue Handys lassen sich nur im Heimnetz oder über das VPN verbinden. Unter *Add-ons → Handy-App* entfernst du ein
Handy; sein Schlüssel gilt dann sofort nicht mehr.

## Was sie kann

- **Dashboard** mit allen Seiten und Einstellungen, von unterwegs über das VPN oder eine öffentliche Adresse.
- **Alarme** kommen über eine eigene verschlüsselte Verbindung zu pi-guard, ohne Google oder Apple dazwischen. Eine
  dauerhafte Benachrichtigung hält sie offen; das kostet etwas Akku.
- **VPN:** Die App ist ein WireGuard-Client. *App-Einstellungen → VPN verbinden* erstellt einen Zugang für dieses Handy.
  Mach das einmal zu Hause. Der gesamte Verkehr läuft dann über dein Zuhause.
- **Handy finden:** unter *Add-ons → Handy-App* oder mit `/finden Name` im Chat. Es klingelt in voller Lautstärke und
  vibriert. Standort, Karte und Details gehen nur raus, wenn der Besitzer *Standort teilen* eingeschaltet hat. Das Handy
  zeigt an, dass jemand es sucht. Die Kartenkacheln kommen von OpenStreetMap, und nur, wenn jemand die Karte öffnet.
- **SOS:** Ein Tippen startet 10 Sekunden Countdown, nochmal tippen bricht ab. Danach bekommen alle Notfallkontakte und
  Admins eine Nachricht mit Standort, Modus, Akku und Netz. **Das ersetzt nicht die 112.** Es braucht eine Verbindung zu
  pi-guard, und pi-guard muss laufen.
- **Anrufen:** Auf der Live-Seite schickt *Anrufen* dein Mikrofon an den Lautsprecher, während du die Kamera hörst. Das
  geht auch im normalen Browser. Rechne mit ein bis zwei Sekunden Verzögerung.
- **Kalender:** Mit *Abwesend während Kalendereinträgen* gelten belegte Termine der nächsten 14 Tage als abwesend, auch
  wenn ein Handy zu Hause ist. Nur Start und Ende gehen an pi-guard, Titel bleiben auf dem Handy. Ganztägige Termine
  zählen nicht.
- **App-Sperre:** PIN und Fingerabdruck oder Gesicht, nach 30 Sekunden im Hintergrund.

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
  App-Sperre und entferne verlorene Handys.
- Das Zertifikat wird beim ersten Start festgelegt. Was nicht von deinem pi-guard oder einer öffentlichen Stelle kommt, wird
  abgelehnt.
