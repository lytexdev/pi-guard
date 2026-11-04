# Signal (optional, unofficial)

Signal has no bot API comparable to Telegram's. pi-guard's Signal support goes
through **[signal-cli](https://github.com/AsamK/signal-cli)**, an unofficial
command-line client that has to be installed, registered and kept up to date
separately. Treat it as an extra with its own maintenance:

- signal-cli must be updated regularly; old versions stop working when Signal
  changes its protocol.
- The account is a real Signal account tied to a phone number. Use a separate
  number for the camera, or link signal-cli as a secondary device of an
  existing account (then messages appear as coming from that account).
- Signal may rate-limit or ask for a captcha when registering or sending many
  messages. Accounts used for automation can be restricted by Signal.
- There are no buttons. Menus are sent as numbered lists; users answer `1`,
  `2`, … or type the command (`foto`, `video 30`).
- Signal groups are not used as command channels. Uploads (voice messages,
  photos) from Signal are not processed.

If signal-cli is down, Signal messages fail and are retried a few times;
recording, detection and Telegram continue normally.

## Install and link

```bash
sudo ./scripts/signal.sh install
```

This installs Java and the latest signal-cli release from GitHub, and starts the service
`pi-guard-signal`. It runs as the `pi-guard` user, because it has to read the photos and videos
pi-guard sends, and listens on `127.0.0.1:7583` only. Keep it current with
`sudo ./scripts/signal.sh update`.

signal-cli only bundles its native library, libsignal, for x86_64 Linux. On a Raspberry Pi the script
fetches a build made by the third-party project
[exquo/signal-libs-build](https://github.com/exquo/signal-libs-build), checks it against the checksum
of that release and installs it. This library handles your Signal keys, so you trust that project too.
To avoid that, build it yourself from
[Signal's source](https://github.com/signalapp/libsignal), as described in the
[signal-cli wiki](https://github.com/AsamK/signal-cli/wiki/Provide-native-lib-for-libsignal).

Then open *Settings → Messenger*, press **Link phone** and scan the code in the Signal app under
*Settings → Linked devices*.

With your own number, pi-guard is a linked device of your account. Write to it in **Note to Self**
and alerts arrive there too. Set your number as system number and as admin number. Messages you
write to other people are ignored.

To register a separate number instead, use signal-cli directly:

```bash
alias sc='sudo -u pi-guard signal-cli --config /var/lib/pi-guard/signal-cli'
sc -a +4915100000000 register          # may ask for a captcha, see signal-cli docs
sc -a +4915100000000 verify 123-456    # code from the SMS
```

While signal-cli is not running, pi-guard logs this once and keeps trying.

## Configure pi-guard

```dotenv
PI_GUARD_SIGNAL_ENABLED=true
PI_GUARD_SIGNAL_PHONE_NUMBER=+4915100000000
PI_GUARD_SIGNAL_ADMIN_NUMBERS=+4917611111111
PI_GUARD_SIGNAL_ALLOWED_NUMBERS=+4917622222222
```

```bash
sudo systemctl restart pi-guard
pi-guard signal test
```

Then write `menu` to the camera's number from an admin phone.

## What differs from Telegram

| | Telegram | Signal |
|---|---|---|
| Menus | buttons | numbered text |
| Photos, videos, audio clips | yes | yes |
| Voice messages to the speaker | yes | no |
| Face enrollment by photo | yes | no |
| Invitations | link + code | code (send it as a message) |
| Group chats | allow-listed groups | no |
| Official API | yes | no (signal-cli) |
