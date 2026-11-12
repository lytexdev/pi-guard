# Mit dem Pi sprechen

Stell dem Pi laut eine Frage und hör die Antwort aus seinem Lautsprecher, wie bei einem smarten Lautsprecher, aber
offline und standardmäßig aus. Englisch: [../voice.md](../voice.md).

## Was es kann

- **Weckwort:** Mit *Einstellungen → Ton → Auf ein Weckwort hören* hört der Pi auf „Hallo lytex“ oder „Hi piguard“
  (*Weckwörter*, mit Kommas getrennt). Die Erkennung läuft auf dem Pi mit [Vosk](https://alphacephei.com/vosk/), gegen
  eine kleine feste Liste. Erfundene Wörter wie *lytex* werden nach ihrem Klang erkannt („lei tex“). Weckwörter, deren
  Wörter das Modell nicht kennt, fallen weg, und das Dashboard sagt es.
- **Push-to-Talk:** *Jetzt mit dem Pi sprechen* auf derselben Seite lässt den Pi einer Frage zuhören, ohne Weckwort.
  Im Chat des Assistenten, im Dashboard, in der Desktop- und Handy-App, fragt der Mikrofon-Knopf über das Gerät in
  deiner Hand, und die Antwort wird dort auch vorgelesen.
- **Knopf:** Ein Taster zwischen einer GPIO-Leitung und Masse, eingetragen als *Sprechknopf an GPIO* (BCM-Nummer,
  0 = keiner). Der Pi liest ihn mit `gpiomon` aus libgpiod 2. Der Dienstnutzer braucht einmal die GPIO-Gruppe:
  `sudo usermod -aG gpio pi-guard`, dann pi-guard neu starten.
- **Namen dieses Zuhauses:** Das kleine Offline-Modell hört Namen wie „Wintergarten“ oder „Stehlampe Flur“ oft nur fast
  richtig. pi-guard setzt nahe Wörter wieder auf die Namen von Kameras, Räumen, Geräten, Befehlen und vertrauten
  Personen (offline, ohne Tokens), aus „schlafzimer“ wird „Schlafzimmer“. Im Chat bekommt eine Cloud-Spracherkennung
  (OpenAI) diese Namen zusätzlich als Hinweis, nie Namen von Menschen. Das Modell selbst wird auf dem Pi nicht neu trainiert.
- **Antwort:** Die Frage geht an das Add-on KI-Assistent, wie eine Frage im Chat, aber ohne deine Dokumente, weil
  niemand weiß, wer spricht. Gesprochen wird mit der Stimme des Assistenten, wenn sein Anbieter eine hat, sonst mit
  `espeak-ng`, falls installiert (`sudo apt install espeak-ng`), sonst steht die Antwort nur auf der Ton-Seite.

## Datenschutz und Sicherheit

- Aus, bis du es einschaltest. Solange das Weckwort an ist, zeigt jede Seite des Dashboards *Hört auf Weckwort* in der
  Navigation, für alle Angemeldeten.
- Ton geht nur durch den Arbeitsspeicher: Weckwort und Frage werden auf dem Pi erkannt, nichts wird aufgenommen oder
  gespeichert. Ein Ton erklingt, wenn der Pi einer Frage zuhört.
- Der Text der Frage geht an den Anbieter des Assistenten, wie jede Frage an den Assistenten (mit Ollama an niemanden).
  Letzte Frage und Antwort bleiben für die Ton-Seite im Arbeitsspeicher bis zum Neustart; das Audit-Log vermerkt nur,
  dass gefragt wurde.
- **Gesprochene Anfragen schalten nie etwas**: kein Modus, kein Alarm, keine Einstellungen, keine Fernbefehle. Jeder am
  Mikrofon kann sprechen, auch durch ein Fenster. Das geht im Dashboard, in der App oder im Chat, wo du angemeldet bist.
- Eine Kamera, die Ton aufnimmt, teilt ihren Mikrofon-Strom, das Mikrofon wird also nicht zweimal geöffnet.

## Einrichtung

```bash
pi-guard voice download        # etwa 45 MB, gegen feste SHA-256 geprüft, Apache-2.0
sudo systemctl restart pi-guard
```

Das Modell folgt der Systemsprache (Deutsch oder Englisch). Der Installer richtet das Paket `vosk` ein, wo es für das
Board eines gibt; sonst bleibt die Sprachsteuerung aus, und alles andere läuft.

## Text sprechen

Auf der Live-Seite sagt *Sprechen* einen getippten Text über den Lautsprecher, für alle, die nicht sprechen können oder
wollen. Es nutzt dieselben Stimmen wie die Antworten.

## Was getestet ist

Mit Unit-Tests: die Schreibweisen der Weckwörter, der geprüfte Modell-Download, Antworten ohne Dokumente und Text
sprechen. Noch nicht auf einem echten Pi mit echtem Mikrofon, Lautsprecher und Knopf getestet.
