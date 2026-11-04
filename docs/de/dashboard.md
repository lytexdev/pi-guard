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

## Mehrere Benutzer

Unter *Einstellungen → Zugang → Dashboard-Benutzer* legst du Personen mit eigenem Passwort an:

| Rolle | Darf |
|---|---|
| Admin | alles |
| Betrachter | Übersicht, Live-Bild, Kameraraum und Ereignisse ansehen, eigenes Passwort und Zwei-Faktor-Code ändern |

Betrachter können nicht mithören, sprechen, Fotos auslösen, den Modus ändern, den KI-Assistenten
nutzen oder Einstellungen öffnen. Das prüft der Server, nicht nur die Oberfläche. Wird eine Person
entfernt, ist sie sofort abgemeldet.

## YubiKey und Passkeys

Der Browser bietet einen Schlüssel nur an, wenn beides stimmt:

1. **Das Dashboard ist über einen Namen geöffnet, nicht über die IP-Adresse**, zum Beispiel
   `https://lytexsystem:8443`. Funktioniert der Name nicht, legt
   `sudo ./scripts/dashboard-name.sh pi-guard.lan` einen Namen in Pi-hole und im Zertifikat an.
   Das ändert die lokalen DNS-Einträge von Pi-hole.
2. **Der Browser vertraut dem Zertifikat.** Die Warnung wegzuklicken reicht nicht: Chrome und
   Chromium lehnen Schlüssel auf Seiten mit Zertifikatsfehler ab. Unter Arch Linux:
   `sudo trust anchor --store pi-guard-ca.crt`, danach den Browser neu starten.

Die Schritte stehen auch unter *Zugang → Hardware-Schlüssel einrichten*. Klappt es nicht, zeigt das
Dashboard die Fehlermeldung des Browsers.

## Werkzeuge im Alltag

- **Geräte ausprobieren:** Unter *Geräte & Kameras* spielt **Lautsprecher testen** drei Töne auf dem gewählten
  Lautsprecher. **Mikrofon testen** nimmt 3 Sekunden zum Anhören auf und zeigt, wie laut es war. Die Aufnahme
  wird nicht gespeichert; der Test wird protokolliert und wie beim Mithören mit dem Gong angekündigt.

- **Tooltips** erklären Knöpfe und Einträge, wenn du mit der Maus darauf zeigst.
- **Navigation:** Unter *Erscheinungsbild → Navigation* ziehst du die Einträge in deine Reihenfolge. Sie
  gilt für dein Konto. Der Kameraraum ist ein Unterpunkt von Live.
- **Wochenplan** (*Einstellungen*): Zieh in einem Tag, wann normalerweise niemand zu Hause ist. pi-guard
  wechselt dann auf Abwesend und zurück. Was du von Hand einstellst, bleibt bis zum nächsten Wechsel im
  Plan. Mit Anwesenheitserkennung bleibt es auf Zuhause, solange jemand erkannt wird.
- **Support** (*Einstellungen*): Name, Telefon, E-Mail und Adresse, alles optional. Erscheint klein in der
  Übersicht, den Einstellungen, den Logs und beim KI-Assistenten.

## Netzwerk-Werkzeuge

- **WLAN-QR-Code:** Der Code-Knopf neben jedem WLAN liest den Schlüssel aus der FRITZ!Box. Du kannst den
  Code als Bild speichern; wer ihn hat, kommt ins WLAN. Ohne FRITZ!Box trägst du Name und Schlüssel ein.
  Der Schlüssel wird nicht gespeichert.
- **Was andere von dir sehen können** erklärt, was dein Anbieter, andere im Netzwerk und der Betreiber
  sehen können, mit dem, was dieser Pi über dein aktuelles Gerät weiß.
- **Ein Gerät aussperren:** Die Schnittstelle der FRITZ!Box kann kein einzelnes Gerät aus dem WLAN werfen.
  Stattdessen gibt es zwei Wege, beide mit deinem Passwort:
  - *Internet sperren* in den Gerätedetails. Das Gerät bleibt im WLAN, kommt aber nicht ins Internet.
  - *Neuer Schlüssel* im Fenster mit dem WLAN-Code. Alle Geräte müssen sich neu anmelden, auch deins. Wer
    den neuen Schlüssel nicht bekommt, kann nur noch ins Gast-WLAN. Hängt der Pi selbst im WLAN, geht das nicht.
- **Die Karte** öffnet sich groß, wenn du auf ihren Hintergrund oder den Vergrößern-Knopf klickst.

## Sicherheitscheck und Speicher

*Einstellungen → Sicherheitscheck* prüft Anmeldung, HTTPS, die pi-guard-Dateien gegen ihre Prüfsummen, offene
Ports, System-Updates, Firewall, SSH, Systemdateien gegen ihre Pakete, unbekannte Programme mit Root-Rechten,
Programme in temporären Ordnern und Konten. Er ändert nichts und kann eine Zusammenfassung per Messenger an
Admins schicken (im Chat auch `/sicherheitscheck`). Ein Rootkit-Scan läuft, wenn `rkhunter` oder `chkrootkit`
installiert ist. Ein gutes Ergebnis ist keine Garantie, dass ein System sauber ist.

*Einstellungen → Speicher* zeigt, was den Platz belegt, und kann alte Aufnahmen, Fotos und Clips löschen, die
Datenbank verdichten sowie alte Systemprotokolle und den Paket-Cache leeren (im Chat auch `/speicher`).
Beweisvideos und Gesichtsdaten werden dort nicht bereinigt.

## Erscheinungsbild

Unter *Einstellungen → Erscheinungsbild* wählst du Akzentfarbe, Schriftart, Animationen und ein
eigenes Logo (PNG, JPEG oder WebP bis 1 MB) für alle, auch für die Anmeldeseite. Farbschema und
Schriftgröße gelten pro Gerät. Schriften werden nicht aus dem Internet geladen.

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
