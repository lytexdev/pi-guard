# Discord (optional)

Ein Discord-Bot kann dasselbe wie der Telegram-Bot: Befehle, Knöpfe, Fotos, Videos und Alarme. Die
Verbindung geht von pi-guard nach außen, es muss also kein Port geöffnet werden.

pi-guard schreibt **nur in Direktnachrichten**. Nachrichten in Server-Kanälen werden ignoriert, weil Bilder
aus deinem Zuhause dort nichts verloren haben. Bilder und Alarme laufen über die Server von Discord.

## Bot erstellen

1. Öffne das [Discord Developer Portal](https://discord.com/developers/applications) und erstelle eine
   **New Application**.
2. Drücke unter **Bot** auf **Reset Token** und kopiere den Token. Behandle ihn wie ein Passwort.
   Client Secret und Public Key auf den anderen Seiten sind etwas anderes und funktionieren nicht.
3. Privilegierte Intents sind nicht nötig.

## Benutzer-ID finden

Schalte in Discord unter *Benutzereinstellungen → Erweitert* den **Entwicklermodus** ein. Klicke dann mit
rechts auf deinen Namen und wähle **Benutzer-ID kopieren**. Schreibst du dem Bot, ohne eingetragen zu sein,
nennt er dir deine ID ebenfalls.

## pi-guard einrichten

Im Dashboard unter *Einstellungen → Messenger → Discord*: Token eintragen, deine Benutzer-ID als Admin
hinzufügen, Discord einschalten, speichern und neu starten.

Admins dürfen alle Befehle nutzen, Betrachter dürfen zuschauen, und beide bekommen Alarme.

## Bot einmal einladen

Discord lässt einen Bot nur Personen anschreiben, die mit ihm auf einem Server sind. Drücke nach dem
Neustart unter *Einstellungen → Messenger* auf **Bot auf einen Server einladen** und füge ihn einem deiner
Server hinzu, zum Beispiel einem eigenen privaten. Er braucht dort keine Rechte. Alle Eingetragenen müssen auf
diesem Server sein und Direktnachrichten von seinen Mitgliedern erlauben.

Drücke dann **Testnachricht senden**, oder schreib dem Bot `menu`. `pi-guard discord test` prüft den Token.

## Grenzen

- Bots dürfen Dateien bis 10 MB senden. Ein größeres Video kommt nur als Text an; kürze den Clip.
- Fotos und Videos an den Bot werden nur von Admins verarbeitet.
