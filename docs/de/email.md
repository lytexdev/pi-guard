# E-Mail (optional)

Verschickt Alarme und Datenexporte per E-Mail. Das ist standardmäßig aus und geht nur in eine Richtung: Befehle
bleiben bei den Chat-Messengern.

Schalte **E-Mail** unter *Add-ons* ein und trag dann den Mailserver unter *Einstellungen → Messenger* ein. Alle
Empfänger bekommen die Alarme, die an alle Benutzer gehen. *Test-Mail senden* prüft die Verbindung.

Bis zu deinem Mailserver ist die Mail verschlüsselt (STARTTLS oder TLS; pi-guard lehnt unverschlüsselte Verbindungen
ab). Danach ist sie nicht Ende-zu-Ende verschlüsselt, und ein Alarmfoto liegt in einem Postfach. Deshalb ist das ein
Add-on.

## Exporte

*Export jetzt senden* schickt alle gespeicherten Daten als JSON-Datei, wie unter *Daten & Sicherung*, ohne Schlüssel,
Tokens und Gesichtsdaten. *Datenexport senden* macht das auf Wunsch jede Woche oder jeden Monat. Die Datei enthält
Ereignisse, Protokolle und Namen.
