# Datenschutz

pi-guard läuft vollständig auf Ihrem Gerät. Hier steht, was gespeichert wird,
was das Gerät verlässt und worauf Sie in Deutschland achten sollten. Dies ist
keine Rechtsberatung. Im Zweifel fragen Sie eine Anwältin, einen Anwalt oder
die Datenschutzbehörde Ihres Bundeslandes.

## Was gespeichert wird

- **Videoaufnahmen** (und Ton, falls eingeschaltet) auf dem Pi, standardmäßig
  7 Tage lang, bei vollem Speicher kürzer.
- **Fotos zu Ereignissen** ebenso lange.
- **Ereignisse, Modus, Benutzer, Protokoll** in einer Datenbank auf dem Pi.
- **Gesichtsdaten** eingetragener Personen (nur wenn die Gesichtserkennung
  eingeschaltet ist), in einer eigenen Datei, bis sie gelöscht werden.

Alle Dateien gehören dem Benutzer `pi-guard` und sind für andere nicht lesbar.

## Was das Gerät verlässt

- Fotos, Videos und Nachrichten, die Sie anfordern oder die als Meldung
  verschickt werden, gehen an **Telegram**. Bot-Chats sind bei Telegram
  *nicht* Ende-zu-Ende-verschlüsselt; die Bilder liegen auf Telegrams Servern.
- Mit Signal (optional) werden sie Ende-zu-Ende-verschlüsselt verschickt.
- Mit MQTT (optional) gehen nur Zustände an Ihren eigenen Server – keine
  Bilder, keine Namen.
- Sonst nichts: keine Cloud, keine Telemetrie, keine Werbung.

## Standardmäßig ausgeschaltet

- Mikrofon / Tonaufnahme
- Personen- und Gesichtserkennung
- Anwesenheitserkennung
- Steuerung über MQTT

## Worauf Sie achten sollten

**Nur den eigenen Bereich filmen.** Die DSGVO gilt nicht für rein private
Tätigkeiten. Diese Ausnahme endet aber, sobald die Kamera auch öffentliche
Flächen erfasst – Gehweg, Straße, das Grundstück der Nachbarn (Europäischer
Gerichtshof, Rechtssache C-212/13). Richten Sie die Kamera so aus, dass sie nur
Ihre Wohnung oder Ihr Grundstück zeigt. Ausschlussbereiche in pi-guard sind
eine zusätzliche Sicherung, kein Ersatz dafür.

**Andere Menschen in der Wohnung informieren.** Besuch, Familie und vor allem
**Pflegekräfte, Haushaltshilfen und Reinigungskräfte** müssen wissen, dass eine
Kamera läuft. Für die Aufnahme von Beschäftigten an ihrem Arbeitsplatz gelten
enge Grenzen; heimliche Aufnahmen sind in aller Regel unzulässig. Ein gut
sichtbarer Hinweis am Eingang ist sinnvoll.

**Gemeinschaftsflächen.** Für Kameras im Treppenhaus, Hausflur oder
Gemeinschaftsgarten brauchen Sie die Zustimmung der anderen Bewohner bzw. der
Eigentümergemeinschaft.

**Ton ist besonders heikel.** Das nichtöffentlich gesprochene Wort anderer
ohne deren Einverständnis aufzunehmen, ist in Deutschland strafbar
(§ 201 StGB). pi-guard nimmt Ton deshalb nur auf, wenn Sie das ausdrücklich
einschalten, nur für eine begrenzte Dauer auf Anforderung eines
Administrators, spielt vorher einen Hinweiston ab (falls ein Lautsprecher
angeschlossen ist) und protokolliert jede Aufnahme.

**Gesichtserkennung nur mit Einwilligung.** Gesichtsdaten sind besonders
geschützte Daten (Art. 9 DSGVO). Tragen Sie nur Personen ein, die ausdrücklich
zugestimmt haben, und halten Sie die Zustimmung fest. pi-guard fragt vor dem
Speichern nach. Das Foto wird sofort gelöscht, gespeichert werden nur
Merkmalswerte. Löschen: `/gesichter löschen Name`. Beim Eintragen werden
Fotos und Videobilder sofort nach dem Auswerten gelöscht; die Gesichtsdaten
bleiben nur im Arbeitsspeicher, bis Sie die Zustimmung bestätigen.
Live-Aufnahmen im Browser oder in der App entstehen nur, wenn Sie die Knöpfe drücken, ohne Ton, und werden
beim Hochladen wie jedes andere Foto behandelt.

**Schärfer und normale Sätze.** „Schärfer“ sucht nur, *wo* eine Person im Bild
ist, nicht *wer* sie ist. Geschriebene Sätze werden auf dem Gerät anhand von
Stichwörtern verstanden; es wird kein Text an einen Sprachdienst geschickt.

**Nicht länger aufbewahren als nötig.** 7 Tage sind ein Ausgangswert. Wenn
Ihnen weniger reicht, stellen Sie weniger ein
(`PI_GUARD_RECORDING_RETENTION_DAYS`).

Mehr Details (Englisch): [privacy.md](../privacy.md).

## Handys mit der App

Der Standort eines Handys wird in `pi-guard.db` nur gespeichert, wenn der Besitzer *Standort teilen* eingeschaltet hat, und
jede neue Meldung ersetzt ihn. Admins können ihn sehen; frag also vorher, bevor du das für jemand anderen einschaltest. Ein
SOS schickt den Standort an die Notfallkontakte, auch über ihre Messenger. Dazu kommt ein aktuelles Bild jeder Kamera (bis zu
vier): Notfallkontakte sehen in diesem Moment also ins Haus. Kartenkacheln holt der Pi, wenn jemand eine Karte öffnet;
OpenStreetMap erfährt dadurch ungefähr, wo das Handy ist, aber nicht, wer schaut.

## Vertraute Personen

Pro Bild werden neben den 128 Zahlen auch Blickrichtung und Helligkeit gespeichert, nicht das Bild selbst.

## Kalender

Kalender-Links (Google, Proton und andere) wirken wie Passwörter: Sie bleiben auf dem Pi und werden nie wieder angezeigt,
protokolliert oder exportiert. pi-guard fragt diese Dienste stündlich ab, sie sehen also die Adresse Ihres Zuhauses.
Gespeichert werden nur Beginn und Ende der Belegt-Zeiten der nächsten zwei Wochen, keine Titel, Orte oder Personen.

## Profile vertrauter Personen

Bild, Geburtstag, Adresse, E-Mail und Telefon sind freiwillig und personenbezogene Daten dieser Person: Tragen Sie sie nur
mit deren Einverständnis ein. Sie liegen bei den Gesichtsdaten, werden mit der Person gelöscht und sind Teil des
Datenexports. Bilder werden verkleinert und neu geschrieben, Standort und Zeit der Originaldatei fallen dabei weg.
