# Training auf dem eigenen Computer

`training/` ist ein Werkzeug für einen Computer mit NVIDIA-Grafikkarte. Es nutzt pi-guards eigenen Erkennungscode mit
den Modellen des Pi. Was es misst, würde der Pi also genauso sehen. Die Bilder bleiben auf diesem Computer.
Ausführlich, mit allen Befehlen: [training.md](../training.md).

| | Wie |
|---|---|
| Bin ich das? Ähnliche Fotos | pi-guards Gesichtsmodelle vergleichen Fotos mit Personen, die du auf diesem Computer anlegst |
| Schwellen der Gesichtserkennung | aus deinen beschrifteten Fotos und den Korrekturen, die Pis anonym schicken |
| Bessere Personenerkennung | YOLOX-Nano, das Modell des Pi, lernt auf deinen Bildern von YOLOX-L |
| Neues Gesichtsmodell | **nicht**: SFace hat an Millionen Gesichtern von Zehntausenden Menschen gelernt. Ein paar hundert Fotos einer Familie würden es für alle anderen schlechter machen |

## Einrichten

```bash
cd training
python3.11 -m venv .venv
.venv/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
.venv/bin/pip install -e "..[vision]" -e ".[gpu,dev]"
.venv/bin/pgtrain models
```

## Gesichter

`.venv/bin/pgtrain ui` gibt eine Adresse wie `http://127.0.0.1:8790/#key=…` aus. Die Seite hat vier Teile:
- Personen mit Fotos anlegen
- *Wer ist das?* für ein Foto
- *Ähnliche Fotos finden* in einem Ordner
- *Schwellen*

Sie ist nur auf diesem Computer erreichbar, und ohne den Schlüssel aus der Adresse kann keine andere Webseite sie
benutzen. Bitte nur Personen anlegen, die zugestimmt haben.

*Schwellen* vergleicht jedes Foto mit den anderen Fotos aller Personen, so wie der Pi ein Gesicht vergleicht. Daraus
zeigt es, wie oft Familie erkannt und wie oft jemand Fremdes für Familie gehalten würde. Vorgeschlagen wird die
niedrigste Schwelle, bei der höchstens einer von 200 Fremden durchkommt. Aussagekräftig wird das ab etwa zehn
Personen mit je zehn Fotos.

## Personenerkennung

```bash
.venv/bin/pgtrain persons train ~/bilder-von-raeumen --devices 2
.venv/bin/pgtrain persons export ~/.local/share/pi-guard-train/runs/persons/best.pth --out yolox_home.onnx
.venv/bin/pgtrain persons evaluate ~/bilder-von-raeumen --model ~/.local/share/pi-guard-train/models/yolox_nano.onnx --model yolox_home.onnx
```

Mit öffentlichen COCO-Bildern allein wurde das offizielle Nano nicht übertroffen (0,602 der Personen gefunden, nach
8 vorsichtigen Runden 0,582). Mit den Bewegungsbildern einer Kamera aber sehr wohl: Auf zwei Tagen, die es nicht
gesehen hatte, fand das trainierte Modell 0,821 statt 0,558 der Personen, und in 87 % statt 61 % der Bilder mit
Personen überhaupt jemanden. Dafür meldete es in 26 statt 5 Bildern jemanden, wo das große Modell niemanden sah,
meist wohl liegende oder halb verdeckte Menschen. Ein solches Modell ist nur für die Wohnung, aus der es gelernt hat. Darum nach dem
Training immer `evaluate` laufen lassen und das Modell nur nehmen, wenn es dort mehr Personen findet.

Beschriften muss man nichts: Das große Modell zeigt dem kleinen in jedem Bild, wo Personen sind. Am meisten helfen
Bilder wie die des Pi: Räume, Flure, Türen, von oben, bei wenig Licht, Personen weit weg oder halb verdeckt.

Die Bewegungsbilder deines Pi auf diesen Computer zu kopieren, entscheidest du als Besitzer. Sie zeigen die Menschen
bei dir zu Hause und Besuch, der nicht in einem Trainingssatz sein wollte. Nutze sie deshalb nur für dein eigenes
Modell, lass sie auf diesem Computer und lösche sie danach. Für ein Modell, das alle bekommen, gehören nur Bilder,
die geteilt werden dürfen: eigene, öffentliche mit passender Lizenz oder gespendete.

## Anonyme Statistiken

Pis, deren Besitzer *pi-guard besser machen* unter Einstellungen → Updates einschalten, schicken einmal pro Woche
einen Bericht an `PI_GUARD_SUPPORT_URL`. Den Empfänger startet `pgtrain reports receive`, hinter HTTPS.
`pgtrain faces calibrate --reports ORDNER` nimmt die Korrekturen aus allen Berichten in die Schwellen mit auf.
