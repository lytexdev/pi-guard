# Talking to the Pi

Ask the Pi a question out loud and hear the answer from its speaker, like a smart speaker, but offline and off by
default. German: [de/sprache.md](de/sprache.md).

## What it does

- **Wake phrase:** with *Settings → Sound → Listen for a wake phrase*, the Pi listens for "Hallo lytex" or
  "Hi piguard" (*Wake phrases*, comma separated). Recognition runs on the Pi with [Vosk](https://alphacephei.com/vosk/),
  against a small fixed list of phrases. Made-up words such as *lytex* are matched by how they sound ("lei tex").
  Phrases whose words the model does not know are left out, and the dashboard says so.
- **Push to talk:** *Talk to the Pi now* on the same page makes the Pi listen for one question, without a wake phrase.
  In the assistant's chat, in the dashboard, desktop and phone app, the microphone button asks through the device you
  hold, and the answer is also read out there.
- **Button:** a push button between a GPIO line and ground, set as *Talk button on GPIO* (BCM number, 0 = none). The
  Pi reads it with `gpiomon` from libgpiod 2. The service user needs the GPIO group once:
  `sudo usermod -aG gpio pi-guard`, then restart pi-guard.
- **Names of this home:** the small offline model hears names such as "Wintergarten" or "Stehlampe Flur" only nearly
  right. pi-guard puts close words back to the names of its cameras, rooms, devices, commands and trusted people
  (offline, no tokens), so "schlafzimer" becomes "Schlafzimmer". In the chat, a cloud speech model (OpenAI) also gets
  those names as a hint, never people's names. The model itself is not retrained on the Pi.
- **Answer:** the question goes to the AI assistant add-on, like a question in the chat but without your documents,
  because nobody knows who is speaking. The answer is spoken with the assistant's voice if its provider has one, else
  with `espeak-ng` if installed (`sudo apt install espeak-ng`), else only shown on the Sound page.

## Privacy and safety

- Off until you switch it on. While the wake phrase is on, every page of the dashboard shows *Listening for wake
  phrase* in the navigation, for everyone signed in.
- Sound only passes through memory: the wake phrase and the question are recognised on the Pi; nothing is recorded or
  stored. A chime plays when the Pi starts to listen to a question.
- The question text goes to the assistant's provider, as any question to the assistant does (none with Ollama). The
  last question and answer stay in memory for the Sound page until a restart; the audit log notes only that a
  question was asked.
- **Spoken requests never switch anything**: no mode, no alarm, no settings, no remote commands. Anyone near the
  microphone can speak, also through a window. Do those things in the dashboard, the app or a chat, where you are
  signed in.
- A camera that records sound shares its microphone stream, so the microphone is not opened twice.

## Setup

```bash
pi-guard voice download        # about 45 MB, checked against a fixed SHA-256, Apache-2.0
sudo systemctl restart pi-guard
```

The model follows the system language (German or English). The installer adds the `vosk` package where a wheel exists
for the board; elsewhere, voice control stays off and everything else works.

## Typed speech

On the Live page, *Speak* says a typed text through the speaker, for anyone who cannot or does not want to talk. It
uses the same voices as the answers.

## What was tested

Tested with unit tests: the wake phrase spellings, the checked model download, answers without documents, and typed
speech. Not yet tested on a real Pi with a real microphone, speaker and button.
