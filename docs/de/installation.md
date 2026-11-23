# Installation

Diese Anleitung richtet pi-guard auf einem Raspberry Pi ein. Sie brauchen dafür
etwa eine halbe Stunde und einen Computer, mit dem Sie sich per SSH auf den Pi
verbinden können.

## Was Sie brauchen

- Raspberry Pi 4 (2 GB reichen) mit Netzteil
- microSD-Karte mit 128–256 GB, möglichst „High Endurance“
- eine USB-Kamera (Webcam)
- optional: ein Telegram-Konto (kann auch später im Dashboard eingerichtet werden)

## 1. Raspberry Pi vorbereiten

1. Laden Sie den **Raspberry Pi Imager** herunter und schreiben Sie
   **Raspberry Pi OS Lite (64-bit)** auf die Karte.
2. Stellen Sie im Imager unter „Einstellungen“ WLAN, Benutzername, Passwort
   und **SSH aktivieren** ein.
3. Karte einlegen, Kamera anstecken, Pi einschalten.
4. Verbinden Sie sich: `ssh benutzer@raspberrypi.local`

## 2. Optional: Telegram-Bot anlegen

Telegram ist nicht Pflicht. Ohne Bot nutzen Sie das Dashboard im Browser oder
die Apps. Wenn Sie Telegram wollen:

1. Öffnen Sie in Telegram **@BotFather** und schicken Sie `/newbot`.
2. Geben Sie einen Namen ein (z. B. „Haus Müller“) und einen Benutzernamen,
   der auf `bot` endet.
3. BotFather schickt Ihnen einen **Token** (`123456789:AAH…`). Das ist ein
   Passwort für den Bot – geben Sie ihn nicht weiter.
4. Öffnen Sie **@userinfobot** und drücken Sie auf Start. Er zeigt Ihnen Ihre
   **Benutzer-ID** (eine Zahl).
5. Öffnen Sie Ihren neuen Bot und drücken Sie auf **Start**.

## 3. pi-guard installieren

```bash
sudo apt install -y git
git clone https://github.com/lytexdev/pi-guard.git
cd pi-guard
sudo ./scripts/install.sh
```

Das Installationsprogramm fragt nach:

- **Sprache**: `de`
- **Zeitzone**: normalerweise `Europe/Berlin` (vorgeschlagen)
- **Bot-Token**: aus Schritt 2, oder leer lassen und später im Dashboard setzen
- **Ihre Telegram-Benutzer-ID**: nur wenn ein Token gesetzt ist
- **Signal**: einfach mit `n` überspringen
- **Anwesenheitserkennung über das WLAN-Handy**: optional, siehe unten
- **Speicherort und Aufbewahrungsdauer** der Aufnahmen: Vorschläge übernehmen
- **Ansprechpartner für Hilfe** (optional): Name, Telefon, E-Mail und Adresse, die klein im Dashboard stehen. Später
  in `/etc/pi-guard/pi-guard.env` als `PI_GUARD_SUPPORT_NAME`, `…_PHONE`, `…_EMAIL`, `…_ADDRESS` änderbar (Werte mit
  Leerzeichen in Anführungszeichen), danach `sudo systemctl restart pi-guard`

Dashboard und Handy-App sind standardmäßig an. Notieren Sie die Zeile
`first login: admin / …` und die Adresse `https://<pi>:8443`. Am Ende sollte
stehen, dass pi-guard läuft und das Dashboard geöffnet werden kann (oder der
Telegram-Bot, wenn Sie einen Token gesetzt haben).

Ein erneutes `install.sh` behält Einstellungen und Aufnahmen und vergibt kein
neues Erstpasswort, wenn die Datenbank schon existiert.

## 4. Erste Schritte im Dashboard

1. Zertifikat von `https://<pi>:8443/ca.crt` auf dem Gerät installieren
   (siehe [Dashboard](dashboard.md#zertifikat-installieren-einmal-pro-gerät)).
2. `https://<pi>:8443` öffnen und als **admin** mit dem Erstpasswort anmelden.
3. Eigenen Benutzernamen und eigenes Passwort wählen (mindestens 10 Zeichen).
   Der Einrichtungsassistent führt durch Kameras, Messenger, Meldungen,
   Notfallkontakte, vertraute Personen, Add-ons und Design. Schritte
   überspringen und später unter *Mehr → Einrichtungsassistent* nachholen.
4. Optional: Android- oder Desktop-App unter *System → Updates*, oder
   Haushaltsmitglieder unter *Kontakte* anlegen und einladen. Am Computer geht auch
   `./scripts/install-desktop.sh` (Linux) bzw. `scripts/install-desktop.ps1` (Windows) ohne Datei: sie laden die
   neueste Version; danach aktualisiert sich die App über diesen Pi. Details: [desktop.md](../desktop.md).
5. *Hilfe & Tutorial* (oder F1 in der Desktop-App) für den Rundgang.

Falls etwas nicht funktioniert:

```bash
pi-guard doctor
```

zeigt, was fehlt; im Dashboard zeigt *System → Zustand*, was gerade nicht läuft. Weitere Hilfe: [Fehlersuche](../troubleshooting.md) (Englisch).

## 5. Angehörige einladen

Im Dashboard unter *Kontakte* legst du eine Person an und schickst ihr mit *Einladen* auf einem ihrer Messenger, was
sie braucht: den Link zum Dashboard, die Apps, Zugangsdaten mit vorläufigem Passwort und einen VPN-Zugang. Ob sie
Admin, Mitglied oder Betrachter ist, legst du dabei fest; die Konten stehen unter *Einstellungen → Zugang*. Per Telegram: **Weitere Funktionen →
Jemanden einladen**; der Bot schickt einen Link. Die Person öffnet ihn und
drückt auf **Start**.

## Anwesenheitserkennung (optional)

pi-guard kann selbst erkennen, ob Sie zuhause sind, wenn Ihr Handy im WLAN ist.
Dann schaltet es automatisch zwischen „Zuhause“ und „Abwesend“ um.

1. Suchen Sie die WLAN-Adresse (MAC-Adresse) Ihres Handys:
   - iPhone: Einstellungen → WLAN → (i) neben dem Netz → „WLAN-Adresse“
   - Android: Einstellungen → WLAN → Netz → Details → „MAC-Adresse“
2. Tragen Sie sie ein:

   ```bash
   sudo nano /etc/pi-guard/config.yaml
   ```

   ```yaml
   presence:
     devices:
       - id: mein_handy
         name: Mein Handy
         mac_address: "AA:BB:CC:DD:EE:FF"
   ```

3. In `/etc/pi-guard/pi-guard.env` muss `PI_GUARD_PRESENCE_ENABLED=true` stehen.
4. `sudo systemctl restart pi-guard`

Soll pi-guard nur automatisch auf „Abwesend“ schalten, aber **nicht** von
selbst wieder auf „Zuhause“, setzen Sie in `/etc/pi-guard/pi-guard.env`:

```
PI_GUARD_PRESENCE_AUTO_HOME=false
```

Kommt dann ein vertrautes Handy zurück ins WLAN, schickt pi-guard eine
Nachricht mit dem Knopf **Ich bin zuhause**. So kann zum Beispiel ein
gestohlenes Handy die Meldungen nicht abschalten.
(`PI_GUARD_PRESENCE_AUTO_AWAY=false` verhindert umgekehrt das automatische
Umschalten auf „Abwesend“.)

Mit einer FRITZ!Box ist die Erkennung zuverlässiger, siehe
[Konfiguration](../configuration.md#how-presence-detection-decides).

Wichtig: Ein Handy im WLAN zeigt nur, dass *wahrscheinlich* jemand da ist.
Ein ausgeschaltetes Handy sieht aus wie „niemand zuhause“.

## Vertraute Personen (optional)

pi-guard kann eingetragene Personen erkennen und meldet unbekannte Personen mit
Foto. Bitte lesen Sie vorher [Datenschutz](datenschutz.md).

1. In `/etc/pi-guard/pi-guard.env`:

   ```
   PI_GUARD_PERSON_DETECTION_ENABLED=true
   PI_GUARD_FACE_RECOGNITION_ENABLED=true
   ```

2. Die Erkennungsmodelle herunterladen (etwa 41 MB, es wird vorher gefragt). Ohne sie
   läuft nur ein viel schwächerer Personendetektor:

   ```bash
   sudo -u pi-guard pi-guard models download
   sudo systemctl restart pi-guard
   ```

3. In Telegram: *Weitere Funktionen → Vertraute Personen → Person hinzufügen*,
   siehe [Bedienung](bedienung.md).
4. Danach im Dashboard unter *Kontakte* *Vor der Kamera lernen* nutzen, damit die Ansichten der eigenen Kamera
   gelernt werden. *Warum die Erkennung klappt oder nicht* zeigt, was noch fehlt.

## Mit dem Pi sprechen (optional)

Sprachsteuerung mit Weckwort, offline auf dem Pi. Sie ist aus, bis du sie einschaltest:

```bash
sudo -u pi-guard pi-guard voice download     # etwa 46 MB, es wird vorher gefragt
```

Dann im Dashboard unter *Add-ons → KI-Assistent* *Auf ein Weckwort hören* einschalten und neu starten. Mehr dazu in
[Sprache](sprache.md).

## Einstellungen ändern

Die wichtigsten Dateien:

- `/etc/pi-guard/pi-guard.env` – Token, Sprache, Funktionen ein/aus
- `/etc/pi-guard/config.yaml` – Kameras, Bewegungsbereiche, Handys, Benachrichtigungen

Nach einer Änderung:

```bash
sudo pi-guard --env-file /etc/pi-guard/pi-guard.env config check
sudo systemctl restart pi-guard
```

## Aktualisieren

```bash
cd pi-guard
sudo ./scripts/update.sh
```

Das installiert die neueste signierte Version. Ein Pi nimmt nur Versionen an, die mit deinem Schlüssel signiert sind, nicht
einfach den Stand von GitHub. Führe vorher kein `git pull` aus: Der Pi wäre sonst der Version voraus, mit Code, den niemand
signiert hat. Mehr dazu in [Updates](../updates.md#updating-the-pi).

## Entfernen

```bash
sudo ./scripts/uninstall.sh
```

Aufnahmen werden dabei nur gelöscht, wenn Sie ausdrücklich `DELETE` eintippen.
