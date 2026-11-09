# Training and tuning on a computer

`training/` is a tool for a computer with an NVIDIA GPU. It runs pi-guard's own detection code with the Pi's models,
so what it measures is what the Pi would do. Pictures stay on that computer.

What it can do, and what not:

| | How |
|---|---|
| Is this me? Similar photos | pi-guard's face models score photos against people you add on this computer |
| Thresholds for face recognition | from your labelled photos and the corrections Pis sent in, see *Thresholds* |
| A better person detector | YOLOX-Nano, the Pi's model, learns from YOLOX-L on your pictures, see *Person detector* |
| A new face model | **not done**: SFace learned from millions of faces of tens of thousands of people. A few hundred photos of one family would make it worse for everybody else |

## Setup

Arch Linux with an NVIDIA driver; `nvidia-smi` must list the GPUs. Python 3.11, because PyTorch has no wheels for
newer versions yet.

```bash
cd training
python3.11 -m venv .venv
.venv/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
.venv/bin/pip install -e "..[vision]" -e ".[gpu,dev]"
.venv/bin/pgtrain models        # about 480 MB, checksums checked
```

Everything goes to `~/.local/share/pi-guard-train` (`PGTRAIN_HOME` changes it): `models/`, `people/`, `runs/`.

The YOLOX network is written out in `src/pgtrain/yolox_model.py` instead of installing the YOLOX package. The official
weights load into it key for key and give the same output as the Pi's ONNX file, within 0.001; a test checks this.
Checkpoints are loaded with `weights_only`, so a checkpoint cannot run code.

## Faces

```bash
.venv/bin/pgtrain ui            # prints http://127.0.0.1:8790/#key=…
```

The page has four parts: add people with photos, *Who is this?* for one photo, *Find similar photos* in a folder of
this computer, and *Thresholds*. It listens on 127.0.0.1 only, and every request needs the key from the address, so
other websites open in the same browser cannot use it. Photos of people are kept in `people/<name>/`; a photo you
check is not kept.

The same on the command line:

```bash
.venv/bin/pgtrain faces compare a.jpg b.jpg
.venv/bin/pgtrain faces whois photo.jpg
.venv/bin/pgtrain faces search ~/.local/share/pi-guard-train/people/Lena ~/Pictures
.venv/bin/pgtrain faces calibrate
```

Add only people who agreed, as on the Pi.

### Thresholds

`calibrate` compares every photo with everybody's other photos, the way the Pi compares a face with the stored
samples, and shows how often family would be recognised and how often a stranger would be taken for family with
the current `match_threshold` and `uncertain_margin`, and with a suggestion. The suggestion is the lowest threshold
at which at most one stranger in 200 passes. It needs photos of at least two people, and gets better with more:
from ten people with ten photos each it starts to mean something.

With `--reports FOLDER` it also counts the corrections Pis sent in: a face an admin confirmed with *This is …* is a
pair of the same person, a face marked *This is not …* a pair of different people. These come from real cameras
across real rooms, which photos from a phone do not show. A new default in `FaceSettings` should rest on both.

## Person detector

```bash
.venv/bin/pgtrain persons train ~/pictures-of-rooms --devices 2
.venv/bin/pgtrain persons export ~/.local/share/pi-guard-train/runs/persons/best.pth --out yolox_home.onnx
.venv/bin/pgtrain persons evaluate ~/pictures-of-rooms --model ~/.local/share/pi-guard-train/models/yolox_nano.onnx --model yolox_home.onnx
```

**Distillation.** For each picture, YOLOX-L (54 million weights, too slow for a Pi) and YOLOX-Nano (0.9 million)
see the same view, and Nano learns to give L's answer in every one of its 3549 grid cells. Nothing has to be
labelled by hand: any folder of pictures works, of any size. The views vary in size, place, mirroring, light and
colour. Nano keeps its 80 classes and its output, so the exported file works on the Pi as it is.

A tenth of the pictures, chosen by file name, is kept for checking. `evaluate` runs each ONNX file through pi-guard's
own detection code and compares it with YOLOX-L at 640 pixels: how many people it finds (`recall`), how many of its
finds are people (`precision`), and in how many pictures with people it finds anyone, which is what decides an event
on the Pi.

What helps is pictures like the Pi's: rooms, hallways, doors, from above, in poor light, people far away or partly
hidden. Pictures of the internet's usual kind teach little the model does not know.

**Measured so far, on public pictures only.** On the 5000 COCO validation pictures (4510 to learn from, 490 to
check), the official Nano was not beaten:

| Model | people found | pictures with people where anyone was found | pictures without people where someone was found |
|---|---|---|---|
| official YOLOX-Nano | 0.602 | 0.871 | 3 |
| 20 rounds, learning rate 2e-4 | 0.543 | 0.835 | 3 |
| 8 rounds, learning rate 2e-5 (now the default) | 0.582 | 0.868 | 6 |

Nano learned COCO from 118,000 labelled pictures; a few thousand of the same kind make it forget more than they
teach, and fast learning makes that worse.

**Pictures of one home help a lot, for that home.** One camera of the owner's Pi, 7616 motion pictures of six days
mixed with the 5000 COCO pictures, 10 rounds at the default settings (31 minutes on two GTX 1660). Checked on the
1295 pictures of the next two days, which it never saw:

| Model | people found | pictures with people where anyone was found | pictures without people where someone was found |
|---|---|---|---|
| official YOLOX-Nano | 0.558 | 0.608 | 5 |
| trained on this camera | 0.821 | 0.874 | 26 |

On COCO it stayed close to the official model (0.571 of the people instead of 0.602). Most of the 26 extra finds
were wide, low boxes where YOLOX-L itself gave 0.3 to 0.5, just under its limit: probably people lying or half
hidden, but about six were below 0.2 and likely wrong. Such a model is for the home it learned from, and it does not
belong in a release, see *Releasing a model for everybody*.

So: train, then `evaluate` on pictures it did not learn from, and use the model only if it finds more people there.

With two GTX 1660 (6 GB each), one round over 2687 pictures took 43 seconds at 16 pictures per GPU, about 62 pictures
a second. Training runs in float32: YOLOX-L overflows to NaN in float16, and a GTX 1660 was slower in it anyway. A
step whose answer is not a number is skipped on all GPUs and counted as `skipped`.

### Pictures from your own Pi

The Pi keeps its motion pictures in `/var/lib/pi-guard/media/snapshots/`. Copying them to this computer is your
decision as the owner. They show the people in your home and visitors who did not choose to be in a training set:
use them only for your own model, keep them on this computer, and delete them when you are done.

```bash
mkdir -p ~/pi-pictures/learn ~/pi-pictures/check
ssh pi 'sudo bash -c "cd /var/lib/pi-guard/media/snapshots && find . -name \"hd_*.jpg\" -print0 | tar --null -T - -cf -"' \
  | tar -xf - -C ~/pi-pictures/learn
```

Pictures a camera takes two seconds apart look almost the same, so a random share for checking would be too
easy. Move the last days into `check/` instead, mix the rest with public pictures so that Nano does not forget what
it knows, and check on each folder as a whole:

```bash
.venv/bin/pgtrain persons train ~/mix --devices 2          # ~/mix: links to learn/ and to public pictures
.venv/bin/pgtrain persons evaluate ~/pi-pictures/check --whole --model …/yolox_nano.onnx --model yolox_home.onnx
```

### Trying a model on the Pi

```bash
scp yolox_home.onnx pi:/tmp/
ssh pi 'sudo install -o pi-guard -g pi-guard -m 0644 /var/lib/pi-guard/models/yolox_nano.onnx /var/lib/pi-guard/models/yolox_nano.onnx.official \
  && sudo install -o pi-guard -g pi-guard -m 0644 /tmp/yolox_home.onnx /var/lib/pi-guard/models/yolox_nano.onnx \
  && sudo systemctl restart pi-guard'
```

Back to the official model: move `yolox_nano.onnx.official` back, or run `pi-guard models download`.

### Releasing a model for everybody

1. Train on pictures you may share: your own, public ones with a suitable licence, or pictures that people gave
   for this purpose. A model can keep traces of what it learned from, so pictures of a private home do not belong in
   a model for everybody.
2. Compare it with the official model on pictures it did not learn from, and keep the numbers.
3. Publish the ONNX file as an asset of a GitHub release, and add it to `src/pi_guard/detection/models.py` with its
   URL and the SHA-256 that `pgtrain persons export` printed. Mention the licence: YOLOX is Apache-2.0, and the model
   inherits the terms of the pictures it learned from.

## Anonymous statistics

Pis whose owners switch on *Help make pi-guard better* (Settings → Updates) send one report a week to
`PI_GUARD_SUPPORT_URL`. What is in it, and what never is: [privacy.md](privacy.md#anonymous-statistics). Until a
receiver runs and the URL is set, nothing is sent.

```bash
.venv/bin/pgtrain reports receive ~/pi-guard-reports --port 8780   # behind HTTPS, e.g. Caddy or nginx
.venv/bin/pgtrain reports summary ~/pi-guard-reports
.venv/bin/pgtrain faces calibrate --reports ~/pi-guard-reports
```

Report version 2 adds, from phones whose users agreed, app and Android versions and crash places;
`reports summary` lists the most frequent crashes. The receiver accepts versions 1 and 2, and only well-formed
reports up to 16 KB, keeps no addresses and writes one file per report into a
folder per week. It needs HTTPS in front, because the Pi sends only to `https://` addresses.
