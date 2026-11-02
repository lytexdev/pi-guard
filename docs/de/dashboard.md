# Web-Dashboard (optional)

Das Dashboard läuft in pi-guard mit und ist standardmäßig aus. Es ist für das
Heimnetz gedacht oder für den Zugriff über ein VPN (WireGuard) – bitte nicht
per Portfreigabe ins Internet stellen.

## Einschalten

In `/etc/pi-guard/pi-guard.env`:

```
PI_GUARD_DASHBOARD_ENABLED=true
PI_GUARD_DASHBOARD_SECRET_KEY=<Ausgabe von: openssl rand -hex 32>
```

`sudo systemctl restart pi-guard`, dann `https://<Adresse-des-Pi>:8443`
öffnen. Telegram muss dafür noch nicht eingerichtet sein.

## Erste Anmeldung und Einrichtungsassistent

Die erste Anmeldung ist **admin / admin123**. Danach verlangt das Dashboard
sofort einen eigenen Benutzernamen und ein eigenes Passwort (mindestens 10
Zeichen) und bietet einen Zwei-Faktor-Code an: QR-Code mit einer
Authenticator-App scannen (z. B. Google Authenticator, Aegis, 2FAS) und einen
Code bestätigen.

Der Assistent führt dann durch: Kameras, Mikrofon und Lautsprecher –
Telegram (Bot-Token, Ihre Benutzer-ID, weitere Benutzer) – Meldungen und
Ruhezeiten – Notfallkontakte – vertraute Personen – Design (dunkel, hell,
hoher Kontrast, große Schrift). Jeder Schritt außer der Anmeldung lässt sich
überspringen und später unter *Mehr → Einrichtungsassistent* wieder öffnen.

**Ausgesperrt?** `sudo -u pi-guard pi-guard dashboard reset-password` setzt
die Anmeldung auf admin / admin123 zurück.

## Anmeldung

- **Benutzername und Passwort** (siehe oben).
- **Schlüsseldatei:** Statt eines Passworts wählen Sie eine Datei aus.
  Erstellen unter *Zugang → Schlüsseldatei erstellen*. Geht ein Gerät verloren,
  löschen Sie die Datei dort.
- Beides zusammen: `PI_GUARD_DASHBOARD_AUTH=password,keyfile`.
- Passwort ändern und Zwei-Faktor-Code ein-/ausschalten: *Zugang → Ihr Konto*.

## Zertifikat installieren (einmal pro Gerät)

Mikrofon, App-Installation und Push-Benachrichtigungen funktionieren nur über
eine vertrauenswürdige HTTPS-Verbindung. pi-guard erstellt dafür ein eigenes
Zertifikat. Laden Sie es unter `https://<Adresse-des-Pi>:8443/ca.crt` (die
Warnung beim ersten Mal ist normal) oder im Dashboard unter *Zugang* herunter:

- **Android:** Einstellungen → Sicherheit → Verschlüsselung und
  Anmeldedaten → Zertifikat installieren → CA-Zertifikat.
- **iPhone/iPad:** Datei öffnen → Einstellungen → Profil geladen →
  Installieren; danach Einstellungen → Allgemein → Info →
  Zertifikatsvertrauenseinstellungen → „pi-guard local CA“ einschalten.
- **Windows:** Doppelklick → Zertifikat installieren → Lokaler Computer →
  Vertrauenswürdige Stammzertifizierungsstellen.
- **Mac:** Doppelklick → Schlüsselbund „System“ → Zertifikat öffnen →
  Vertrauen → Immer vertrauen.

## Als App auf dem Handy

- **Android:** Browser-Menü → „App installieren“.
- **iPhone (ab iOS 16.4):** Teilen → „Zum Home-Bildschirm“. Push-Meldungen
  gibt es auf dem iPhone nur in der installierten App.

Push einschalten: `PI_GUARD_WEBPUSH_ENABLED=true`, dann auf jedem Gerät unter
*Mehr → Diese App → Meldungen auf diesem Gerät erhalten*.

## Was es kann

- **Übersicht:** Modus, Kameras, offene Meldungen mit „Alles in Ordnung“,
  Speicher, Temperatur, die letzten 24 Stunden.
- **Live:** Live-Bild (mit zwei Fingern oder dem Mausrad vergrößern), Foto,
  „Schärfer“, Vergrößern in neun Bereiche, „Heller“, Mithören, Sprechen über
  den Lautsprecher (Knopf gedrückt halten), Töne, Alarm, Lautstärke.
- **Ereignisse:** mit Filter, Vorschaubildern und Diagrammen.
- **Personen:** vertraute Personen mit Fotos, Videos oder Kamerabild
  hinzufügen, umbenennen, löschen.
- **Netzwerk:** Pi-hole, WireGuard und Geräte im Heimnetz
  ([Netzwerk](netzwerk.md)).
- **Geräte:** Kamera, Auflösung, Mikrofon und Lautsprecher als Auswahlfelder.
- **Einstellungen, Zugang, Logs.**

Sprache unter *Mehr* umschalten. Gespeicherte Einstellungen landen in
`dashboard.env`/`dashboard.yaml` im Datenverzeichnis und haben Vorrang vor
`/etc/pi-guard`. Nach dem Speichern bietet das Dashboard einen Neustart an;
die Aufnahme pausiert dabei kurz.

## Schutz

Sitzungen mit sicheren Cookies, Schutz gegen fremde Seiten (CSRF), strenge
Sicherheits-Header, keine Aufnahme in Suchmaschinen, Begrenzung der Anfragen,
Sperre nach mehreren falschen Anmeldungen und „Honeypots“, die automatische
Angreifer-Programme erkennen und sperren.
