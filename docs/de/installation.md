# Installation

Diese Anleitung richtet pi-guard auf einem Raspberry Pi ein. Sie brauchen dafür
etwa eine halbe Stunde und einen Computer, mit dem Sie sich per SSH auf den Pi
verbinden können.

## Was Sie brauchen

- Raspberry Pi 4 (2 GB reichen) mit Netzteil
- microSD-Karte mit 128–256 GB, möglichst „High Endurance“
- eine USB-Kamera (Webcam)
- ein Telegram-Konto auf Ihrem Handy

## 1. Raspberry Pi vorbereiten

1. Laden Sie den **Raspberry Pi Imager** herunter und schreiben Sie
   **Raspberry Pi OS Lite (64-bit)** auf die Karte.
2. Stellen Sie im Imager unter „Einstellungen“ WLAN, Benutzername, Passwort
   und **SSH aktivieren** ein.
3. Karte einlegen, Kamera anstecken, Pi einschalten.
4. Verbinden Sie sich: `ssh benutzer@raspberrypi.local`

## 2. Telegram-Bot anlegen

1. Öffnen Sie in Telegram **@BotFather** und schicken Sie `/newbot`.
2. Geben Sie einen Namen ein (z. B. „Haus Müller“) und einen Benutzernamen,
   der auf `bot` endet.
3. BotFather schickt Ihnen einen **Token** (`123456789:AAH…`). Das ist ein
   Passwort für den Bot – geben Sie ihn nicht weiter.
4. Öffnen Sie **@userinfobot** und drücken Sie auf Start. Er zeigt Ihnen Ihre
   **Benutzer-ID** (eine Zahl). Diese brauchen Sie gleich.
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
- **Bot-Token**: aus Schritt 2 (wird beim Eintippen nicht angezeigt)
- **Ihre Telegram-Benutzer-ID**: die Zahl von @userinfobot
- **Signal**: einfach mit `n` überspringen
- **Anwesenheitserkennung über das WLAN-Handy**: optional, siehe unten
- **Speicherort und Aufbewahrungsdauer** der Aufnahmen: Vorschläge übernehmen

Danach prüft pi-guard alles selbst. Am Ende sollte stehen:
`pi-guard is running. Open your bot in Telegram and press Start.`

## 4. Ausprobieren

Schreiben Sie Ihrem Bot `menü` oder drücken Sie auf **Start**. Es erscheinen
die Knöpfe **Foto ansehen**, **Video ansehen**, **Kamerastatus**,
**Benachrichtigungen** und **Hilfe**.

Falls etwas nicht funktioniert:

```bash
pi-guard doctor
```

zeigt, was fehlt. Weitere Hilfe: [Fehlersuche](../troubleshooting.md) (Englisch).

## 5. Angehörige einladen

Tippen Sie auf **Weitere Funktionen → Jemanden einladen**. Der Bot schickt
einen Link. Schicken Sie diesen Link der Person (z. B. per SMS). Sie öffnet ihn,
drückt in Telegram auf **Start** und kann dann Fotos und Videos ansehen.

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
