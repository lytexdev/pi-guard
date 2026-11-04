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
einen Knopf – oder **Alle Kameras** für ein Foto von jeder.

## Einfach schreiben

Sie können dem Bot auch ganz normal schreiben, zum Beispiel:

- „Kannst du mir bitte ein Foto schicken?“
- „Schick mir ein Video vom Eingang“
- „Das Bild ist zu dunkel“ – „Kannst du das vergrößern?“
- „Ich gehe jetzt weg“ – „Ich bin wieder da“
- „Funktioniert alles?“ – „Was ist passiert?“

Ist ein Satz nicht eindeutig, fragt der Bot mit Knöpfen nach, statt zu raten.

## Fotos genauer ansehen

Unter jedem Foto gibt es drei Knöpfe:

- **Vergrößern** – zeigt neun Pfeile (↖ ↑ ↗ ← · → ↙ ↓ ↘). Tippen Sie auf den
  Bildteil, den Sie näher sehen möchten. Das geht auch mehrmals hintereinander.
  Es ist ein digitaler Zoom: Sehr kleine Details bleiben unscharf.
- **Schärfer** – die Kamera macht mehrere Bilder kurz hintereinander, und Sie
  bekommen das schärfste. Ist eine Person zu sehen, wird auf sie vergrößert.
- **Heller** – für dunkle Bilder, zum Beispiel am Abend.

Das funktioniert auch mit dem Foto aus einer Bewegungsmeldung.

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
Lautsprecher abgespielt, als Ton gespeichert oder als **Alarmton** verwendet
werden.

**Alarm** (mit Lautsprecher): *Weitere Funktionen → Alarm*. Der Bot fragt noch
einmal nach, dann ertönt der Alarm 30 Sekunden lang, auch in der Ruhezeit.
Mit **Alarm stoppen** oder `alarm aus` hört er sofort auf.

**Vertraute Personen** (wenn die Gesichtserkennung eingerichtet ist):
*Weitere Funktionen → Vertraute Personen → Person hinzufügen*. Danach sagt pi-guard, wie gut die
Person eingeprägt ist und welche Bilder als Nächstes helfen.

1. Den Namen schreiben.
2. Fotos oder ein kurzes Video schicken, auf denen nur diese Person zu sehen
   ist – oder die Person stellt sich vor die Kamera und Sie tippen auf
   **Bild mit der Kamera aufnehmen**. Mehrere Bilder aus verschiedenen
   Blickwinkeln helfen.
3. **Fertig** drücken und bestätigen, dass die Person einverstanden ist.

Meldet die Kamera eine unbekannte Person, die Sie kennen, tippen Sie unter der
Meldung auf **Als vertraute Person eintragen**.

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
| `schärfer`, `vergrößern links`, `heller` | Foto verbessern |
| `foto alle` | ein Foto von jeder Kamera |

Die vollständige Liste steht in [telegram.md](../telegram.md) (Englisch).
