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

## Install signal-cli

signal-cli needs Java 21 (or use the native build where available). On
Raspberry Pi OS 64-bit:

```bash
sudo apt install -y openjdk-21-jre-headless   # or the version your release offers
VERSION=0.13.x   # pick the current release from the GitHub releases page
wget https://github.com/AsamK/signal-cli/releases/download/v$VERSION/signal-cli-$VERSION.tar.gz
sudo tar xf signal-cli-$VERSION.tar.gz -C /opt
sudo ln -sf /opt/signal-cli-$VERSION/bin/signal-cli /usr/local/bin/signal-cli
```

signal-cli runs as the `pi-guard` user, because it has to read the photos and
videos pi-guard sends:

```bash
sudo install -d -o pi-guard -g pi-guard -m 0700 /var/lib/pi-guard/signal-cli
alias sc='sudo -u pi-guard signal-cli --config /var/lib/pi-guard/signal-cli'
```

### Option A: register a separate number

```bash
sc -a +4915100000000 register          # may ask for a captcha, see signal-cli docs
sc -a +4915100000000 verify 123-456    # code from the SMS
sc -a +4915100000000 updateProfile --name "pi-guard"
```

### Option B: link to an existing account

```bash
sc link -n pi-guard        # prints an sgnl:// link; turn it into a QR code
```

Scan the QR code in the Signal app under *Settings → Linked devices*.

The numbers can also be entered in the dashboard under *Settings → Messenger → Signal*.

## Run the daemon

```bash
echo "SIGNAL_ACCOUNT=+4915100000000" | sudo tee /etc/pi-guard/signal.env
sudo install -m 0644 deploy/systemd/pi-guard-signal.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pi-guard-signal
```

The daemon listens on `127.0.0.1:7583` only.

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
