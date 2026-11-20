# Verbundene Haushalte

Ein pi-guard kann einen anderen betreuen: den der Großmutter, der Eltern, eine Zweitwohnung. Die helfende Person sieht
den Zustand des Haushalts, bekommt seine Alarme und jedes SOS und öffnet sein Dashboard, ohne in seinem WLAN oder VPN zu
sein. Beim betreuten Haushalt muss nichts geöffnet werden. Englisch: [Linked households](../federation.md).

```text
Betreuter Pi (Oma)  ──── eine ausgehende, verschlüsselte Verbindung ────►  Pi der helfenden Person
  kein offener Port                                                       Port 8444 weitergeleitet
```

Alles ist aus, bis es eingeschaltet wird. Jede Verbindung hat eine Richtung: *hilft* und *wird betreut*. Zwei Haushalte,
die sich gegenseitig helfen, haben zwei Verbindungen.

## Einrichten

Auf dem Pi der **helfenden Person** (einmal):

1. *Einstellungen → Verbundene Haushalte*: *Verbundene Haushalte nutzen* und *Andere Haushalte dürfen sich hierher
   verbinden* einschalten, pi-guard neu starten.
2. Im Router **TCP-Port 8444** an diesen Pi weiterleiten. Dort antwortet nur die Verbindung, mit eigenem Zertifikat; das
   Dashboard ist dort nie erreichbar. *Öffentliche Adresse* ist, wie der andere Pi deinen erreicht: ein DynDNS-Name, oder
   leer für den öffentlichen Namen des VPN. Es zählt nur der Name: ein eingefügtes `https://name/` oder
   `name:8445` wird auf den Namen gekürzt, der Port ist `federation_port`. Die Sperrseite von Pi-hole (`blockpage_ip`) benutzt selbst 8088 und 8444:
   Mit ihr einen anderen Port wählen, etwa 8445. Die Einstellungen lehnen einen Port des Dashboards oder der Sperrseite
   ab, und die Seite und *System → Zustand* sagen, wenn der Port belegt ist.
3. *Code erstellen* (mit deinem Passwort). Der Code gilt **einmal und 24 Stunden**. Schick ihn auf einem sicheren Weg,
   etwa per Signal, oder tipp ihn bei der Oma selbst ein.

Auf dem **betreuten** Pi (zum Beispiel beim Einrichten bei der Oma):

1. *Verbundene Haushalte nutzen* einschalten (nicht den zweiten Schalter) und pi-guard neu starten.
2. *Code eingeben*, einfügen und festlegen, was die helfende Person darf:
   - *Alles einstellen (Admin)* oder *Nur ansehen*;
   - *Alarme und Zustand*: Alarme als Nachricht an ihre Admins (ein SOS kommt immer an);
   - *Kamerabilder und Aufnahmen*: ohne Haken bleiben Bilder, Aufnahmen und Gesichter im Haushalt;
   - *Notfalldaten*: das Add-on Notfalldaten.
3. Mit dem Passwort bestätigen. Der Pi verbindet sich sofort und danach von selbst, auch nach einem Neustart.

Beide Seiten zeigen einen kurzen Schlüssel (`dd26 7660 af33 5c9a`). Wer sie am Telefon vergleicht, weiß, dass niemand
dazwischen sitzt.

## Benutzen

Auf der Seite der helfenden Person steht jeder Haushalt mit seinem Zustand: verbunden, zuhause oder abwesend, laufende
Kameras, offene Meldungen, das letzte Ereignis, die Version. *Dashboard öffnen* öffnet das Dashboard des Haushalts in
einem neuen Tab, mit einem Hinweis oben, wessen es ist, und dem Weg zurück. Es geht wie vor Ort, mit den Rechten, die der
Haushalt vergeben hat. Alarme und SOS kommen als Nachricht an die Admins der helfenden Person, höchstens eine pro
Haushalt und Art in der Minute.

Nie über die Verbindung, egal was erlaubt ist: Anmeldedaten, Passkeys, Schlüsseldateien, API-Tokens, die Konten,
Sicherungen, Exporte, Geräte-Updates, Live-Bild und -Ton und die Verbindungen selbst. Was ein Passwort braucht, geht nur
vor Ort: Die helfende Person hat es nie. Der betreute Haushalt kann die Rechte jederzeit ändern oder die Verbindung
trennen; die helfende Person kann sie auch trennen. Trennen beendet eine offene Verbindung sofort.

## Haushalte als Kontakte

Ein verbundener Haushalt ist auch ein Weg, jemanden zu erreichen, wie ein Messenger: Bei den Wegen eines Kontakts wählt
*Verbundener Haushalt* eine der Verbindungen. So ein Kontakt kann jede Rolle haben: zweite Instanz, Notfallkontakt,
bekommt Alarme, SOS, vertraute Person. *Als Kontakt* bei einer Verbindung öffnet das Kontaktformular mit dem Haushalt
schon eingetragen. Das geht auf beiden Seiten: Die helfende Person kann den betreuten Haushalt eintragen und der
betreute Haushalt die helfende Person.

Was durchgeht: nur der Text der Nachricht. Er erreicht die Admins des anderen Haushalts auf ihren Messengern, unter dem
Namen des Haushalts; eine dringende Nachricht (Alarm, Eskalation, SOS) kommt dort wie ein SOS an, auf jedem Messenger.
Bilder, Ton, Dateien und das Notfalldatenblatt bleiben zu Hause. Ein Haushalt, der mehr als 20 Nachrichten in
10 Minuten schickt, wird gebremst. Ist ein Haushalt gerade nicht verbunden, wird er übersprungen wie ein Messenger, der
nicht läuft. Einladungen mit Zugangsdaten gehen nie über eine Verbindung: Sie kämen bei den Admins des anderen Haushalts
an statt bei der Person.

## Wie es geschützt ist

- Der betreute Pi baut die Verbindung auf: TLS zu einem Zertifikat, das im Code **festgelegt** ist (keine
  Zertifizierungsstelle beteiligt), auf einem Port, der sonst nichts anbietet.
- Bei jeder Verbindung beweisen beide Pis ihren **Ed25519**-Schlüssel über frische Zufallswerte, gebunden an ihre Rolle.
  Der betreute Pi spricht nur mit dem Schlüssel aus dem Code; die helfende Seite nimmt nur Schlüssel an, die sie
  verbunden hat. Die Schlüssel entstehen einmal und liegen versiegelt mit dem geheimen Schlüssel des Dashboards.
- Vom Code wird nur ein Hash gespeichert, und er gilt einmal; Fehlversuche von einer Adresse sind begrenzt.
- Eine Anfrage der helfenden Person prüft der betreute Pi zweimal: nach den Regeln der Verbindung, dann wie jede Anfrage,
  als Benutzer `fed:<id>` mit dem vergebenen Recht, und sie steht in seinem Protokoll.
- Im Browser der helfenden Person läuft nur ihre **eigene** pi-guard-Seite: Ein Service Worker für `/fed/<id>/` schickt
  die API-Anfragen der Seite durch die Verbindung, und die Antworten sind nur Daten. Seiten, Skripte oder SVG eines
  betreuten Pi werden nie unter der Adresse der helfenden Person gezeigt; ein eingebrochener Haushalt kommt so nicht an
  ihren Pi.

## Grenzen

- Der Pi der helfenden Person muss von außen auf seinem Port erreichbar sein (Portweiterleitung, oder eine Adresse mit
  IPv6). Der betreute Pi braucht das nicht.
- Das betreute Dashboard nutzt die Seitenversion der helfenden Person. Beide sollten dieselbe Version haben; signierte
  Updates halten sie nah beieinander.
- Live-Video geht nicht durch die Verbindung; mit *Kamerabilder* sieht die helfende Person Schnappschüsse und Aufnahmen.
- Getestet in einem Prozess mit zwei pi-guard, echter TLS-Verbindung und Browser (Chromium). Zwischen zwei echten
  Haushalten noch nicht getestet.
