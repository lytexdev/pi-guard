# Web-Dashboard

Das Dashboard läuft in pi-guard mit. Es und die Handy-App sind standardmäßig an;
die Installation richtet sie ein. Es ist für das Heimnetz gedacht oder für den
Zugriff über ein VPN (WireGuard) – bitte nicht per Portfreigabe ins Internet
stellen.

## Einschalten

Ausschalten in `/etc/pi-guard/pi-guard.env`:

```
PI_GUARD_DASHBOARD_ENABLED=false
PI_GUARD_MOBILE_ENABLED=false
```

`sudo systemctl restart pi-guard`, dann `https://<Adresse-des-Pi>:8443`
öffnen. Telegram muss dafür noch nicht eingerichtet sein.

## Erste Anmeldung und Einrichtungsassistent

Die erste Anmeldung ist **admin** mit dem Passwort, das `install.sh` am Ende zeigt. Ohne das Skript,
etwa in Docker, ist es **admin123**, wenn du nicht `PI_GUARD_DASHBOARD_FIRST_PASSWORD` setzt. Danach
verlangt das Dashboard
sofort einen eigenen Benutzernamen und ein eigenes Passwort (mindestens 10
Zeichen) und bietet einen Zwei-Faktor-Code an: QR-Code mit einer
Authenticator-App scannen (z. B. Google Authenticator, Aegis, 2FAS) und einen
Code bestätigen.

Der Assistent führt dann durch (jeder Schritt außer der Anmeldung lässt sich
überspringen und später unter *Mehr → Einrichtungsassistent* wieder öffnen;
was die Installation schon erledigt hat, erscheint als *Bereits eingerichtet*):

1. Willkommen und Sprache
2. Anmeldung und Zwei-Faktor-Codes (Pflicht, solange das erste Passwort gilt)
3. Kameras, Mikrofon und Lautsprecher
4. Messenger: Telegram, und bei Bedarf Discord, E-Mail, WhatsApp oder Signal
5. Meldungen und Modi: auch zuhause, Video statt Foto, Ruhezeiten, Anwesenheit
6. Notfallkontakte
7. Vertraute Personen: Erkennung einschalten, Modelle laden, Personen eintragen
8. Add-ons: optionale Teile einschalten
9. Apps: welche Apps dieser Pi anbietet, ein Download-Link mit QR-Code zum Scannen mit dem Handy (einige Tage gültig),
   wie man die App verbindet, und ein Hinweis fürs iPhone (Dashboard zum Home-Bildschirm)
10. Design: dunkel, hell oder hoher Kontrast, normale oder große Schrift
11. Fertig: Neustart, wenn sich Einstellungen geändert haben

**Mitglieder und Betrachter** bekommen den Assistenten nicht. Nach der ersten
Anmeldung ändern sie Passwort und Zwei-Faktor unter *Zugang*. *Hilfe & Tutorial*
zeigt nur die Seiten ihrer Rolle (für Betrachter: Übersicht, Live und Ereignisse).

**Ausgesperrt?** `sudo -u pi-guard pi-guard dashboard reset-password` setzt
die Anmeldung auf admin mit einem neuen Passwort zurück, das der Befehl anzeigt.

## Anmeldung

- **Benutzername und Passwort** (siehe oben).
- **Schlüsseldatei:** Statt eines Passworts wählen Sie eine Datei aus.
  Erstellen unter *Zugang → Schlüsseldatei erstellen*. Geht ein Gerät verloren,
  löschen Sie die Datei dort.
- Beides zusammen: `PI_GUARD_DASHBOARD_AUTH=password,keyfile`.
- Passwort ändern und Zwei-Faktor-Code ein-/ausschalten: *Zugang → Ihr Konto*.
- Das Codewort kann einen Tipp haben. Er erscheint bei der Anmeldung erst nach dem richtigen Passwort und darf
  das Codewort nicht enthalten.
- **Code per Messenger oder E-Mail** (`code` in `PI_GUARD_DASHBOARD_AUTH`): ein sechsstelliger Code an deine eigenen
  Wege, also an die verknüpften Admin-Konten in Signal, Discord, WhatsApp, Telegram, der Handy-App oder einer Chat-App,
  oder an die E-Mail-Adresse in den Kontaktdaten deines Kontos, nie an Empfänger von Meldungen. Er gilt 5 Minuten und
  5 Versuche. Angeboten wird er nur, wenn ein Codewort oder Zwei-Faktor-Codes eingerichtet sind, und die werden danach
  gefragt: Ein übernommenes Postfach oder Chat-Konto allein reicht nie.
- **Passkeys und OpenPGP:** Sobald unter *Zugang → Anmeldung absichern* einer hinterlegt ist, erscheint sein Knopf
  auf der Anmeldeseite. Bei OpenPGP zeigt die Seite einen kurzen Text; signiere ihn, etwa mit `gpg --clearsign`, und
  füge das Ergebnis ein.
- **Sicherheitsfragen:** bis zu fünf, mit freier Antwort oder Auswahl und optionalem Tipp. Bei jeder Anmeldung wird eine
  gefragt, immer zusätzlich zum Codewort und nie statt ihm, und für einen Anmeldeversuch bleibt es dieselbe.
  Groß- und Kleinschreibung zählt nicht; gespeichert wird nur ein Hash.
- **Kontaktdaten** (*Zugang → Kontaktdaten*, für Admins auch neben jedem Benutzer): Name, E-Mail, Telefon, Adresse,
  Notiz, alles freiwillig, mit vCard zum Einlesen und Speichern. Sie ziehen beim Umbenennen mit, werden mit dem Konto
  gelöscht und sind Teil des Datenexports.

## Mehrere Benutzer

Unter *Einstellungen → Zugang → Dashboard-Benutzer* legst du Personen mit eigenem Passwort an:

| Rolle | Darf |
|---|---|
| Admin | alles |
| Mitglied | was Betrachter sehen, dazu was ein Admin unter *Rechte* anhakt: Bereiche (Zuhause/Abwesend und Alarm, Personen, Netzwerk, Logs, Kontakte, Geräte, Apps), einzelne Add-ons und einzelne Einstellungsbereiche |
| Betrachter | Übersicht, Live-Bild, Kameraraum und Ereignisse ansehen, eigenes Passwort und Zwei-Faktor-Code ändern |

Betrachter können nicht mithören, sprechen, Fotos auslösen, den Modus ändern, den KI-Assistenten
nutzen oder Einstellungen öffnen. Das prüft der Server, nicht nur die Oberfläche; bei Mitgliedern ebenso, Pfad für Pfad.
Zugang, Anmeldungen, Tokens, Backups, Updates, Sicherheitscheck und eigene Add-ons lassen sich nie freigeben. Der
Verknüpfungs-Knopf neben einer Person öffnet ihre Kontaktdaten und Verknüpfungen: Telegram, Discord, Signal, WhatsApp,
eine vertraute Person der Kameras und ihre Notfallkarte. Wird eine Person
entfernt, ist sie sofort abgemeldet, und ihre Handys, Computer und VPN-Zugänge sind weg. Ein neues
Passwort meldet alle anderen Browser dieser Person ab; verbundene Handys melden sich beim nächsten
Öffnen selbst wieder an. Einen Passkey, einen API-Token oder eine Schlüsseldatei anzulegen fragt nach
dem Passwort, beim Passkey nach dem des Hauptkontos.

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

## Add-ons und Module

Telegram, Signal, Discord, WhatsApp, E-Mail und die Chatdienste sind Module unter *Add-ons*. Ein ausgeschaltetes Modul
verschwindet aus *Einstellungen → Messenger* und stoppt beim nächsten Neustart; seine Einträge bleiben erhalten. Was zu
pi-guard gehört, trägt das Abzeichen *Verifiziert*. Das Menü jeder Karte (drei Punkte) heftet sie an die Navigation,
zeigt Hilfe und den Weg zur Tour und entfernt sie. Das Plus neben der Suche fügt eigene Add-ons hinzu. Die Handy-App
ist kein Modul: Sie ist standardmäßig an, unter *System → Updates*, mit den Versionen, die dieser Pi weitergibt, und
Einladungslinks (sieben Tage, nur Downloads, gespeichert als Hash) für Leute, die die Android- oder Desktop-App bekommen
sollen.

Abschnitte in den Einstellungen klappst du per Klick auf die Überschrift auf und zu; das bleibt in diesem Browser so.
Messenger, die aus sind, starten zugeklappt.

## Vertraute Personen

Das Formular erklärt, was die Erkennung gut macht. **Jetzt Fotos oder Video aufnehmen** öffnet die Kamera des
Geräts, das du gerade benutzt, und führt die Person durch Blick geradeaus und leicht zur Seite (ein kleiner Kopf neben
dem Text zeigt, wohin), oder filmt
8 Sekunden ohne Ton. Antippen entfernt ein Bild; erst **Hochladen** schickt etwas an den Pi. Das braucht die
HTTPS-Adresse und deine Erlaubnis für die Kamera, sonst bietet die Seite die Kamera-App des Handys an. Du kannst
auch vorhandene Dateien wählen oder eine Kamera zu Hause nutzen.

Ein Balken zeigt, wie gut jede Person eingeprägt ist, mit den Bildern je Blickrichtung und dem nächsten
sinnvollen Schritt. Der Wert ist eine Schätzung aus Anzahl der Bilder, Blick von vorne und zur Seite, Licht und
wie gut die Bilder zusammenpassen; Ähnlichkeit in der Familie senkt ihn. Den echten Eindruck gibt der Test mit
einem Foto.

Leicht zur Seite gedrehte Gesichter helfen, weil kaum jemand in die Kamera schaut. Beide Augen müssen zu sehen
bleiben: Ein reines Seitenprofil können die Erkennungsmodelle nicht nutzen. Früher eingelernte Bilder zählen als
Blick von vorne.

### Aus der Kamera lernen

Fotos vom Handy und Bilder einer Kamera quer durch den Raum sehen für die Erkennungsmodelle verschieden aus.
**Wer ist das?** an einem Personen-Ereignis, in der Übersicht oder unter Ereignisse, zeigt die Gesichter, die
pi-guard in dessen Bild findet, und wen es jeweils gesehen hat:

- **Das ist …** speichert das Gesicht als weiteres Bild einer Person, die schon auf der Seite Personen steht, so
  lernt pi-guard Blickwinkel und Licht der Kamera. Neue Personen kommen über die Seite Personen dazu, mit ihrer
  Zustimmung. Das Gesicht muss mindestens 56 Pixel breit, scharf und etwa zur Kamera gewandt sein; ein kleineres
  brächte mehr Rauschen als Gesicht bei. Ein Gesicht, das den gespeicherten Bildern der Person kaum ähnelt oder
  eher jemand anderem, wird ab dem dritten Bild der Person abgelehnt; das fängt einen falschen Klick ab. Die ersten
  beiden nimmt pi-guard so, wie du es sagst. In den Details der Person sind diese
  Bilder als *von der Kamera* markiert.
- **Das ist nicht …** an einem erkannten Gesicht merkt es sich als jemand anderen. Danach ist ein Gesicht, das ihm
  ähnlicher ist als dieser Person, *unsicher* statt diese Person. Es wird mit der Person gelöscht.

- **Wer ist das?** oben auf der Seite Personen geht die Gesichter der Personen-Ereignisse der letzten zwei Wochen wie
  einen Kartenstapel durch: nach rechts wischen (oder →) für *ja, das ist die vorgeschlagene Person*, nach links (←) für
  *nein* bzw. *fremd*, nach oben (↑) zum Überspringen, oder einen Namen unter dem Bild antippen (Tasten 1–9), wenn es
  jemand anderes ist. Ja und Nein wirken wie *Das ist …* und *Das ist nicht …* oben, mit denselben Prüfungen; Gesichter,
  die zu klein oder unscharf zum Lernen sind, fehlen. Ein weggewischtes Gesicht kommt nicht wieder. Wählbar sind nur
  Personen, die schon eingetragen sind.
- **Vor der Kamera lernen** auf der Karte einer Person: Die Person steht 20 Sekunden lang dort, wo man sonst
  vorbeigeht, schaut zur Kamera und dreht den Kopf etwas. Der Pi nimmt jede Sekunde ein Bild und behält bis zu acht
  deutliche Gesichter, die sich von den bekannten Ansichten unterscheiden; jedes Bild wird gelöscht, sobald sein
  Gesicht vermessen ist. Gesichter, die nicht zueinander oder zur Person passen, fallen weg, so wird niemand gelernt,
  der zufällig vorbeiläuft. Bei anderem Licht wiederholen, etwa abends. Nur für bereits eingetragene Personen, mit
  ihrer Zustimmung.

Eine Person behält bis zu `faces.max_samples_per_person` Bilder (Standard 40, einstellbar unter den Zahlen auf der Seite
Personen, gilt sofort). Ein Bild, das einem gespeicherten fast
gleicht, wird weggelassen, und ist die Grenze erreicht, fällt das ähnlichste weg statt des ältesten: Verschiedene
Ansichten erkennen jemanden auch in anderem Blickwinkel und Licht.

**Warum die Erkennung klappt oder nicht** auf der Seite Personen zeigt die letzten 30 Tage in Zahlen: Momente mit einer
Person, wie viele davon überhaupt ein Gesicht zeigten, wie groß Gesichter waren, wie nah das beste an die Schwelle kam,
und je Person, wie viele Bilder von einer Kamera stammen und wie viele fast gleich sind. Sie sagt, was am meisten hilft.
Oft ist es die Kamera: Die Erkennung braucht ein Gesicht im Bild, etwa zur Kamera gewandt und mindestens 36 Pixel breit,
zum Lernen 56.

Gespeichert werden wie beim Eintragen nur die Zahlen eines Gesichts. Zuschauer sehen die Knöpfe nicht.

## Kontakte

*Kontakte* hat einen eigenen Eintrag in der Navigation, unter *Netzwerk*. Dort stehen alle Personen als Karten: Rollen
und Chips für das Verknüpfte (Gesicht mit Anzahl der Bilder, Dashboard-Konto, Messenger, Notfallkontakt, Notfallkarte,
VPN-Geräte), mit Suche und Rollenfilter. Das Bild zeigt die Initialen; *Profilbild ändern* (auf das Bild tippen oder im
Menü) setzt ein eigenes Foto ein, im Browser quadratisch zugeschnitten und verkleinert, *Standardbild verwenden* nimmt es
wieder heraus. Ein farbiger Rahmen zeigt die Rechte: rot Admin, grün Mitglied, blau nur ansehen, lila zweite Instanz,
orange Notfallkontakt, gelb bekommt Alarme; die Legende steht unter den Karten.

Jede Karte hat *Bearbeiten* (das Setup unten), *Fotos & Videos* (Bilder und Videos für die Gesichtserkennung hochladen
oder die Kamera nutzen, mit Einverständnis der Person; gespeichert werden nur Gesichtsmerkmale) und *Einladen*. Eine
Einladung geht an einen eigenen Kontaktweg der Person (Messenger oder E-Mail, nie an eine eingetippte Adresse), und du
wählst den Inhalt: Link zum Dashboard, Android-App, Desktop-Apps (Download-Links 7 Tage gültig), Zugangsdaten und einen
eigenen VPN-Zugang als WireGuard-Datei. Zugangsdaten enthalten ein neues vorläufiges Passwort, das erst gespeichert wird,
wenn die Nachricht raus ist; das alte gilt dann nicht mehr. Zugangsdaten und VPN-Zugang fragen nach deinem Passwort, und
der Dialog sagt, welche Dienste die Nachricht behalten. Klappt das Senden nicht, wird ein neuer VPN-Zugang wieder
entfernt. Mitglieder brauchen neben *Kontakte* den Bereich *Apps* für die App-Links und *Netzwerk* für einen VPN-Zugang;
Zugangsdaten verschicken nur Admins, und nie für das Konto des Besitzers. *Noch nicht zugeordnet* bietet Gesichter,
Konten und Notfallkontakte ohne Person an, je ein Tipp. Die Gesichtserkennung im Detail und die Notfallkontakt-Liste sind
darunter eingeklappt.

*Neue Person* öffnet ein kurzes Setup (wer, Rollen, laufende Messenger, was bei SOS oder unbeantworteten Alarmen ankommt,
optional Dashboard-Konto, Gesichtsproben und optional Notfallkarte). Ein neues Konto bekommt ein vorläufiges Passwort:
vom Pi erzeugt (empfohlen) oder von dir eingetippt. Es wird einmal angezeigt, mit *Einladung senden*; beim ersten
Anmelden muss die Person ein eigenes Passwort wählen, vorher öffnet sich nichts anderes. Speichern nutzt die
Admin-Sitzung; die Notfallkarte als PDF braucht weiterhin eine Bestätigung.

*Wer ist das?* oben ordnet Gesichter aus Ereignissen per Wischen einer Person zu oder markiert Fremde. Alle Kontakte
stehen zur Wahl: wen die Kameras noch nicht kennen, steht unter *Noch nicht angelernt*, und sein erstes Gesicht wird
erst genommen, wenn du bestätigst, dass die Person zugestimmt hat; das Gesicht wird dann dem Kontakt zugeordnet. Die alten
Adressen *Personen* und *Einstellungen → Kontakte* leiten hierhin um. Wer Telegram/Signal/… für Befehle nutzen darf,
steuerst du hier; die alten Admin-/Betrachter-Listen unter Nachrichten bleiben nur im Expertenmodus.

### Rollen und wer was bekommt

- **Zweite Instanz** (Rolle): Menschen im Haushalt, die nach einem Alarm schauen, wenn kein Admin reagiert hat. Ein
  Alarm, der nach *Zweite Instanz informieren nach (Minuten)* (Standard 10, `emergency_backup_minutes`) noch offen ist,
  geht an ihre Messenger, noch vor den Notfallkontakten; die Admins erfahren, wer informiert wurde.
- **Notfallkontakt** und **bekommt Alarme**: Nachricht bei einem SOS oder wenn Meldungen unbestätigt bleiben.
- *Einstellungen → Benachrichtigungen* zeigt alles als Treppe mit vier Stufen, jede dort einstellbar:
  1. **Sofort an euch:** pro Weg, was er von sich aus schickt, zuerst Handy-App und Browser-Push, dann jeder Messenger
     (Alarme und Notfälle, nur eins davon oder *nur Befehle*), und pro Meldung wann, an wen und über welche Wege.
     Alarme nur in der App und der Rest auch per Messenger: die App auf *Alarme und Notfälle*, die Messenger auf *nur
     Notfälle*; dort kommen dann zweite Instanz, Eskalationen und SOS an. Browser-Push lässt sich direkt dort
     einschalten; ein Alarm, der nur per Push rausging, zählt für die nächsten Stufen trotzdem als offen.
  2. **Zweite Instanz:** nach wie vielen Minuten ein unbestätigter Alarm an die Menschen mit dieser Rolle geht.
  3. **Notfallkontakte:** nach wie vielen offenen Alarmen und Minuten sie informiert werden, wer mitmacht und was
     mitkommt (letzte Ereignisse, Bilder vom Pi).
  4. **SOS:** immer sofort an alle Admins auf jedem Messenger, an verbundene Haushalte und an die Kontakte, die
     mitmachen, mit dem, was bei ihnen angekreuzt ist (Standort, Fotos und Ton vom Handy, Bilder vom Pi, Ton von zu
     Hause, Notfalldaten). Ein Hinweis erscheint, wenn die zweite Instanz erst nach den Notfallkontakten käme.
- *Einstellungen → Notfall* zeigt die Notfallkontakte und für wen Notfalldaten hinterlegt sind, über den
  Eskalations-Einstellungen. Der Sicherheitscheck liegt unter *Einstellungen → Daten & Sicherung → Sicherheitscheck*.

## System

*System* in der Navigation hat drei Reiter. **Zustand** prüft den laufenden Dienst alle 30 Sekunden, ohne die Kamera
anzufassen: pi-guard und seine Teile, jede Kamera (Zustand, letztes Bild, Aufnahme), Speicher, Arbeitsspeicher,
Prozessor, Temperatur, die Stromversorgung des Pi (Unterspannung und Drosselung, jetzt oder seit dem Start), die
Datenbank, jeden Messenger, die Erkennungsmodelle, die letzte Sicherung und die Uhrzeit; ein Problem verlinkt dorthin,
wo es behoben wird. Mehr prüft `sudo pi-guard doctor` (FFmpeg, Encoder, Kameras). Oben steht ein Ring, wie viele
Prüfungen in Ordnung sind, mit *Neu prüfen*, darunter Kacheln für Last, Arbeitsspeicher, Temperatur und freien Platz
mit Balken, dann eine Karte je Prüfung, Probleme zuerst. **Gerät** zeigt Modell, Prozessor,
Arbeitsspeicher, Betriebssystem, Kernel, Laufzeit, die Daten- und Aufnahme-Laufwerke, Kameras und Tongeräte,
USB-Geräte und die Adressen im Netz. **Updates** steht unten. Mitglieder sehen die Systemseite nur für Updates, wenn
sie Apps verwalten dürfen; Zustand und Gerät bleiben bei Admins.

## Updates und Versionen

*System → Updates* zeigt die Version des Pi (sie steht auch neben dem Hinweis unten in der Navigation), die
neueste Version, woher Updates kommen (Repository und Kanal Stabil/Beta), die Instanz-ID dieser Installation, alle
App-Versionen (*Nach App-Updates suchen*), Einladungen und Telefone. Die anonymen Statistiken sind vorerst aus.
Dasselbe steht unter *Einstellungen → System*.

*Jetzt aktualisieren* zeigt die echten Schritte des Updates, wie der Root-Job sie meldet: angefordert, signierte Version
holen und prüfen, installieren, neu starten (die Seite wartet, solange pi-guard weg ist, und sagt es, wenn die alte
Version zurückkommt), fertig, mit *Seite neu laden*. Ein fehlgeschlagener Schritt wird rot, mit der Meldung des Jobs.
*Nach Updates suchen* zeigt, dass es sucht, und danach kurz *Neue Version gefunden* oder *Keine neue Version*.
Die alten Adressen *System → Updates* und *Einstellungen → Updates* leiten hierhin um.

## Werkzeuge im Alltag

- **Geräte ausprobieren:** Unter *Geräte & Kameras* spielt **Lautsprecher testen** drei Töne auf dem gewählten
  Lautsprecher. **Mikrofon testen** nimmt 3 Sekunden zum Anhören auf und zeigt, wie laut es war. Die Aufnahme
  wird nicht gespeichert; der Test wird protokolliert und wie beim Mithören mit dem Gong angekündigt.

- **Tooltips** erklären Knöpfe und Einträge, wenn du mit der Maus darauf zeigst.
- **Navigation:** Unter *Erscheinungsbild → Navigation* ziehst du die Einträge in deine Reihenfolge. *Netzwerk*
  steht standardmäßig in der Hauptleiste. *Logs* liegen unter *Einstellungen → Logs* (`#/logs` leitet um).
  Der Kameraraum ist ein Schalter oben auf der Live-Seite.
- **Wochenplan** (*Einstellungen → Zuhause & Abwesend*): Zieh in einem Tag, wann normalerweise niemand zu Hause ist. pi-guard
  wechselt dann auf Abwesend und zurück. Was du von Hand einstellst, bleibt bis zum nächsten Wechsel im
  Plan. Mit Anwesenheitserkennung bleibt es auf Zuhause, solange jemand erkannt wird.
- **Support** (*Einstellungen → Support*): Name, Telefon, E-Mail und Adresse, alles optional. Erscheint klein in der
  Übersicht, den Einstellungen, den Logs und beim KI-Assistenten. Eingetragen wird es bei der Installation oder in der
  env-Datei (`PI_GUARD_SUPPORT_*`), nicht im Dashboard: Die Seite zeigt es nur an. Was eine ältere Version im Dashboard
  gespeichert hat, erscheint, bis die env-Datei jemanden nennt.
- **Logs:** *Einstellungen → Logs* filtert nach genau einem Level (INFO zeigt keine WARNING). Bekannte
  aiohttp-Meldungen „SSL connection is closed“ werden auf etwa eine pro Minute gedrosselt.

## Netzwerk-Werkzeuge

- **WLAN-QR-Code:** Die WLAN-Karte im Netzwerk-Überblick hat zwei Zeilen, *Heim-WLAN* und *Gast-WLAN*. Mit einer
  FRITZ!Box kommen beide von dort, und *Teilen* liest den Schlüssel aus ihr. Ohne FRITZ!Box *hinterlegst* du jedes
  einmal (Name und Schlüssel, mit deinem Passwort); der Schlüssel liegt mit dem geheimen Schlüssel des Dashboards
  versiegelt und wird nur für den Code geöffnet. Du kannst den Code als Bild speichern; wer ihn hat, kommt ins WLAN.
- **Was andere von dir sehen können** erklärt, was dein Anbieter, andere im Netzwerk und der Betreiber
  sehen können, mit dem, was dieser Pi über dein aktuelles Gerät weiß.
- **Ein Gerät aussperren:** Die Schnittstelle der FRITZ!Box kann kein einzelnes Gerät aus dem WLAN werfen.
  Stattdessen gibt es zwei Wege, beide mit deinem Passwort:
  - *Internet sperren* in den Gerätedetails. Das Gerät bleibt im WLAN, kommt aber nicht ins Internet.
  - *Neuer Schlüssel* im Fenster mit dem WLAN-Code. Alle Geräte müssen sich neu anmelden, auch deins. Wer
    den neuen Schlüssel nicht bekommt, kann nur noch ins Gast-WLAN. Hängt der Pi selbst im WLAN, geht das nicht.
- **Die Karte** öffnet sich groß, wenn du auf ihren Hintergrund oder den Vergrößern-Knopf klickst.

## Sicherheitscheck und Speicher

*Einstellungen → Daten & Sicherung → Sicherheitscheck* prüft Anmeldung, HTTPS, die pi-guard-Dateien gegen ihre Prüfsummen, offene
Ports, System-Updates, Firewall, SSH, Systemdateien gegen ihre Pakete, unbekannte Programme mit Root-Rechten,
Programme in temporären Ordnern und Konten. Er ändert von sich aus nichts und kann eine Zusammenfassung per Messenger an
Admins schicken (im Chat auch `/sicherheitscheck`). Ein Rootkit-Scan läuft, wenn `rkhunter` oder `chkrootkit`
installiert ist; Meldungen, die jedes pi-guard hat (Pi-hole-Tabellen in `/dev/shm`, der pi-guard-Benutzer in Gruppen),
zählen als bekannt. Ein gutes Ergebnis ist keine Garantie, dass ein System sauber ist.

**Korrekturen:** Unter dem Ergebnis stehen passende Korrekturen aus einer festen Liste: SSH-Anmeldung als root sperren,
SSH nur mit Schlüssel (abgelehnt ohne hinterlegten Schlüssel), automatische Sicherheitsupdates, rkhunter-Stand
übernehmen, Firewall einschalten (alles heute Erreichbare und SSH bleiben offen). Jede läuft im Root-Job mit Sicherung,
nach zwei Bestätigungen, dem eingetippten Namen und dem Passwort; *Rückgängig* stellt den alten Zustand her. Die
**KI-Prüfung** schickt nach Bestätigung Status und kurze Details an den KI-Anbieter. Sie läuft im Hintergrund, man kann
die Seite verlassen, und zeigt ihren echten Stand. Das Ergebnis bleibt bis zur nächsten Prüfung: eine Gesamteinschätzung,
Schritte nach Wichtigkeit (wichtig, mittel, gering) mit dem Check, um den es geht, ein Knopf, wenn eine Korrektur der
Liste es erledigt, und andere Befehle eingeklappt zum Kopieren, nie ausgeführt. Ist die Prüfung älter als der letzte
Check, steht das dabei.

Der Root-Job meldet sich nach jedem Schritt, der Fortschritt zeigt also den Schritt, an dem er wirklich ist. Stürzt ein
Schritt ab oder laufen seine Befehle in eine Zeitüberschreitung, stehen seine Checks auf *unbekannt* mit dem Grund statt
auf *ok*; die anderen Schritte zählen trotzdem. pi-guard wartet, solange der Job Fortschritt meldet (30 Minuten ohne,
zwei Stunden insgesamt).

*Einstellungen → Daten & Sicherung → Speicher* zeigt, was den Platz belegt, und kann alte Aufnahmen, Fotos und Clips löschen, die
Datenbank verdichten sowie alte Systemprotokolle und den Paket-Cache leeren (im Chat auch `/speicher`).
Beweisvideos und Gesichtsdaten werden dort nicht bereinigt.

## Wenn das Dashboard langsam wirkt

Öffne *Logs* und suche nach `slow request:`. Dort steht jede Antwort, die eine halbe Sekunde oder länger gedauert
hat, mit ihrer Route. Antworten des Assistenten tauchen ebenfalls auf, weil sie ohnehin dauern.

## Erscheinungsbild

Unter *Einstellungen → Erscheinungsbild* wählst du Akzentfarbe, Schriftart, Animationen und den Namen der App für
alle, auch für die Anmeldeseite. Das Logo ist das von pi-guard und lässt sich nicht mehr ändern (die Handy-App kann sich
weiter tarnen). Farbschema (wie System, dunkel, hell, hoher Kontrast) und Schriftgröße gelten pro Gerät; das helle
Schema hält bei jeder Textfarbe mindestens 4,5:1 Kontrast. *Auf Standard zurücksetzen* stellt alles nach einer
Rückfrage wieder her. Schriften werden nicht aus dem Internet geladen. Die Handy-App (ab 1.17) übernimmt das
Farbschema auch für ihre eigenen Bildschirme und die Systemleisten. In der Übersicht ist der gewählte Modus
(*Zuhause* oder *Abwesend*) gefüllt und mit Haken markiert.

## Logs

*Logs* hat vier Reiter: einen Überblick der letzten sieben Tage (Anmeldungen, Fehlversuche, Honeypot, Sperren,
Empfehlungen), das Dienst-Log mit Stufen-Filtern, Suche, Anhalten, Kopieren und Herunterladen, das Protokoll nach
Tagen gruppiert und durchsuchbar, und **KI-Blick**. Dort liest der Assistent nach Bestätigung die Warnungen und Fehler
des Dienst-Logs (Wiederholungen zu einer Zeile mit Anzahl zusammengefasst, lange Tokens gekürzt) und die letzten 80
Einträge der Aktivität und antwortet mit einer Einschätzung und Funden nach Schwere, jeweils mit Bedeutung und was zu tun
ist. Nur für Admins; er ändert nichts.

## Barrierefreiheit

Der runde Knopf unten rechts öffnet auf jeder Seite die Bedienungshilfen, nur für diesen Browser: Textgröße, Buchstaben-
und Zeilenabstand, umgekehrte Farben, Graustufen, Filter für Rot-Grün- und Blau-Gelb-Farbenblindheit, großer Mauszeiger,
Leselinie, eine Lupe für den Text unter dem Zeiger, unterstrichene Links, weniger Animationen, Vorlesen von angetipptem
Text oder der ganzen Seite und eine Tour. Die Textgröße wirkt zusätzlich zur großen Schrift im Design; Zeilen wie bei den
Ereignissen brechen um, statt die Wörter zu quetschen. Die WebView der Handy-App hat keine Sprachausgabe: Vorlesen sagt
das dort. Sperrt der Browser seinen Speicher (privates Fenster), gelten die Einstellungen nur für diese Seite.

Das **Vollbild** der Live-Kamera nutzt das Vollbild des Browsers, wo es erlaubt ist. Auf dem iPhone (Vollbild nur für
Videos) und in der Handy-App füllt es stattdessen das Fenster; der runde Knopf, Escape oder die Zurück-Taste schließen es.

## Hilfe und Tutorial

*Hilfe & Tutorial* in der Navigation erklärt jeden Teil von pi-guard in wenigen Sätzen, jeweils mit Knopf dorthin, und
*Rundgang starten* geht sie Seite für Seite durch; Betrachter sehen nur ihre Seiten. Die Kapitel sind: Übersicht, Live,
Ereignisse, Kontakte, Netzwerk, VPN, Kid Protect, Benachrichtigungen, Notfallkontakte, Notfalldaten, Apps, Assistent,
Umzug, System und Einstellungen. Dasselbe Tutorial gibt es überall:

- in der **Desktop-App** unter *PiGuard › Hilfe & Tutorial* (F1) und in der **Handy-App** unter *Mehr → Hilfe*;
  beide öffnen diese Seite mit dem Rundgang;
- im **Assistenten**: „Tutorial“ oder „Rundgang“ im Chat des Dashboards, getippt oder gesprochen, startet den Rundgang,
  und Fragen wie „Wie funktioniert Kid Protect?“ beantwortet er aus dem Tutorial, ohne KI-Anbieter und kostenlos;
- in **Chats**: `/tutorial` (auch *Anleitung*, *Rundgang*) schickt die Übersicht.

- **Für Gehörlose und Schwerhörige:** *Alarme sichtbar machen* lässt die Seite bei jedem neuen Alarm leuchten und das
  Gerät vibrieren. Es pulsiert langsam und blinkt nie schnell.
- **Für Menschen, die nicht sprechen können:** *Sprechen* auf der Live-Seite sagt einen getippten Text über den
  Lautsprecher zu Hause.
- **Für Blinde:** Das Dashboard ist für Screenreader wie NVDA, JAWS, VoiceOver und TalkBack gebaut: Bedienelemente
  haben Beschriftungen, Änderungen werden angesagt, und *Zum Inhalt springen* erreicht die Tastatur zuerst. Mit jedem
  dieser Screenreader getestet ist es noch nicht; melde bitte, was nicht klappt. Mit
  *Vorlesen* werden auch Antworten des Assistenten vorgelesen.
- **Per Sprache oder Chat:** im Chat des Assistenten, getippt oder gesprochen: „Schrift größer“, „Vorlesen an“, „Lupe
  aus“, „Rot-Grün“, „Sichtbare Alarme an“, „zurücksetzen“. Das passiert im Browser und wird nirgends hingeschickt. Mit
  dem Pi selbst sprechen: [sprache.md](sprache.md).

## Normal- und Expertenansicht

In den Einstellungen wechselt ein Schalter neben Überschrift und Suche zwischen **Normal** und **Experte**. Normal zeigt, was die meisten
brauchen, Experte alles. Das gilt pro Gerät, auch in der Handy-App, und versteckte Einstellungen behalten ihre Werte.

Im Expertenmodus endet *Messenger* mit *Testnachrichten*: eine Testnachricht über jeden eingerichteten Messenger, an
jeden Admin dort oder in den Kanal von Slack, Matrix, Rocket.Chat oder Mastodon. Add-ons, die zu pi-guard gehören,
tragen ein Häkchen; Darüberfahren, Fokus oder Antippen zeigt *Verifiziert*.

Die Einstellungen sind in Grundlagen, Benachrichtigen, Geräte & Zugang und System (mit Add-ons) gruppiert. Jeder Bereich beginnt
mit einer Zeile, was er enthält, und jede Einstellung zeigt ihre Erklärung direkt unter dem Namen. Ein/Aus ist ein
Schalter. *Daten & Sicherung* hat vier Tabs: *Backup*, *Speicher*, *Umzug* und in der Expertenansicht *Export*; alte
Links (`#/settings/backup`, `/storage`, `/move`, `/data`) öffnen den passenden Tab.

Seit 1.18: *Erkennung* und die Ton-Schalter (Ton aufnehmen, Ton abspielen, größte Lautstärke) gehören zu *Geräte &
Kameras*, unter die Geräte, zu denen sie gehören. Weckwörter, der Sprechknopf und *Mit dem Pi sprechen* stehen auf der
Seite des Add-ons KI-Assistent. **System** zeigt den Zustand des Pi in einer Zeile (Details und Hardware auf der
Systemseite) und die Updates von pi-guard und den Apps mit ihren Einstellungen. **Netzwerk** enthält Router, Pi-hole,
VPN und dynamisches DNS, mit Links zu den Netzwerk-Seiten und den Add-ons Cloudflare DDNS und Tailscale. *Integrationen*
(Expertenansicht) behält MQTT und die gespeicherten Schlüssel. Alte Links (`#/settings/detection`, `/audio`, `/updates`)
öffnen den neuen Ort.

## Der Chat des Assistenten

Mit eingeschaltetem KI-Assistenten öffnet der runde Knopf unten rechts seinen Chat: am Computer als Fenster neben
der Seite, am Handy über den ganzen Bildschirm. Er beginnt mit drei Beispielen zum Antippen, zeigt Punkte, solange
der Pi nachdenkt, stellt Listen und Fettschrift in Antworten dar, fragt vor jeder Aktion *Ja* oder *Nein* und zeigt
Fehler im Chat selbst. *Neues Gespräch* leert ihn; das Mikrofon nimmt eine gesprochene Frage auf, wo der Browser es
erlaubt.

## Fragen zu Einstellungen und Änderungen rückgängig machen

*Einstellungen → Allgemein* beginnt mit *Frag zu Einstellungen*: „Was macht die Bewegungsempfindlichkeit?“ oder
„Aufnahmen 14 Tage behalten“. Ohne KI-Assistent zeigt es die Einstellungen, die zu deinen Worten passen, mit Hilfetext und
Wert. Mit Assistent erklärt es sie auch und kann eine Änderung vorschlagen, angezeigt als *vorher → nachher*. Es ändert
sich nichts, bis du *Ändern* tippst und bestätigst.

Was die KI bekommt: Bezeichnungen, Hilfetexte und die Werte von Schaltern, Zahlen und Auswahlen. Nie Geheimnisse, Listen
wie Chat-IDs, Adressen oder die Zeitzone. Einstellungen zu Anmeldung, Dashboard-Zugang, VPN, Updates und dem Assistenten
selbst werden erklärt, aber nie vorgeschlagen. Ein Vorschlag durchläuft dieselben Prüfungen wie das Formular.

*Letzte Änderungen* zeigt jede Änderung, aus dem Formular, vom Assistenten oder durch Rückgängig, mit wer sie gemacht hat
und dem Wert vorher und nachher. *Rückgängig* stellt die früheren Werte wieder her; ein Wert, der vorher nicht gesetzt war,
wird wieder entfernt, sodass `pi-guard.env` gilt. Geänderte Geheimnisse werden vermerkt, aber nie gespeichert, und lassen
sich daher nicht rückgängig machen. Die letzten 200 Änderungen bleiben. Wie jede Einstellung wirkt eine Änderung nach einem
Neustart.

Unter Einstellungen › Benachrichtigungen legst du bei jeder Nachricht fest, **wohin** sie geht: unter *Alle Wege*
kannst du zum Beispiel nur Signal wählen, oder Telegram und die PiGuard-App. Das wirkt sofort.

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
