# Bedienung

pi-guard wird über Telegram bedient. Sie müssen sich keine Befehle merken:
Alles geht über Knöpfe.

## Das Menü

Schreiben Sie dem Bot `menü` oder drücken Sie auf **Zurück zum Menü**:

```
pi-guard

Ihre Kamera funktioniert.

Die Aufzeichnung ist eingeschaltet.

Was möchten Sie machen?

[ Foto ansehen ]
[ Video ansehen ]
[ Kamerastatus ]
[ Benachrichtigungen ]
[ Hilfe ]
```

- **Foto ansehen** – ein Bild von gerade eben.
- **Video ansehen** – die letzten 15 Sekunden. Unter dem Video gibt es
  „Längeres Video“ für eine Minute.
- **Kamerastatus** – ob alles funktioniert und wann zuletzt eine Bewegung war.
- **Benachrichtigungen** – Meldungen für Sie ein- oder ausschalten.
- **Hilfe** – eine kurze Erklärung.

Mit **Zurück zum Menü** kommen Sie immer zum Anfang.

Gibt es mehrere Kameras, fragt der Bot „Welche Kamera?“ und zeigt für jede
einen Knopf.

## Zuhause und Abwesend

- **Zuhause**: Bewegungen werden aufgezeichnet und notiert, aber Sie bekommen
  keine Nachricht. So werden Sie nicht von Ihren eigenen Bewegungen gestört.
- **Abwesend**: Bei Bewegung bekommen Sie eine Nachricht mit Foto.

Umschalten über **Weitere Funktionen** → **Ich bin zuhause** /
**Ich gehe weg**, oder schreiben Sie:

| Sie schreiben | Bedeutung |
|---|---|
| `zuhause` | Modus „Zuhause“ |
| `abwesend` | Modus „Abwesend“ |
| `abwesend 3h` | drei Stunden „Abwesend“, danach wieder wie vorher |
| `modus` | zeigt den aktuellen Modus |
| `modus auto` | wieder automatisch über das Handy im WLAN (falls eingerichtet) |

Was Sie selbst einstellen, hat immer Vorrang vor der automatischen Erkennung.

## Wenn eine Meldung kommt

Eine Bewegungsmeldung sieht so aus:

> Bewegung erkannt: Wohnzimmer, 14:32 Uhr.
>
> [ Video ansehen ] [ Neues Foto ]
> [ Ich bin zuhause ]
> [ Zurück zum Menü ]

Waren Sie es selbst, tippen Sie auf **Ich bin zuhause**. Es kommen dann keine
weiteren Bewegungsmeldungen mehr.

Technische Meldungen („Die Kamera … ist nicht erreichbar“) gehen nur an
Administratoren.

## Für Administratoren

**Weitere Funktionen** bietet zusätzlich:

- **Letzte Ereignisse** – was zuletzt passiert ist
- **Kameras** – Zustand und Einstellungen jeder Kamera
- **Benutzer** / **Jemanden einladen** – Angehörige hinzufügen oder entfernen
- **Ton abspielen** – falls ein Lautsprecher angeschlossen ist
- **Einstellungen** – Sprache und Ansicht (einfach/erweitert)

Sprachnachrichten, die ein Administrator dem Bot schickt, können über den
Lautsprecher abgespielt werden.

## Befehle zum Eintippen

Wer lieber tippt: Groß- und Kleinschreibung und der Schrägstrich sind egal.

| Befehl | |
|---|---|
| `foto`, `bild`, `p` | Foto |
| `video`, `v`, `video 30` | Video der letzten Sekunden |
| `status`, `s` | Kamerastatus |
| `ereignisse` | letzte Ereignisse |
| `benachrichtigungen aus` / `an` | eigene Meldungen |
| `sprache de` / `sprache en` | Sprache |
| `hilfe` | Hilfe |

Die vollständige Liste steht in [telegram.md](../telegram.md) (Englisch).
