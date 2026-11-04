# Add-ons

Optional extras, off by default. Choose them in the setup assistant or under
**Add-ons** in the dashboard; each needs a restart of pi-guard once. They are stored
under `addons:` in the configuration. Everything works in the dashboard and in every
messenger, where buttons become a numbered menu if the platform has none.

| Add-on | Command | Aliases |
| --- | --- | --- |
| Service watcher | `/services` | `/dienste` |
| Network analysis | `/netstats [ip]` | `/analyse` |
| Smart home | `/smarthome [name] [on\|off]` | `/smart`, `/schalte` |
| Remote commands | `/action` | `/aktion` |
| AI assistant | `/ask question`, `/weather [place]` | `/frage`, `/wetter` |

All but the assistant are for admins.

## Service watcher

Watches websites (HTTP), ports (TCP) and hosts (ping). After several failed checks in a
row you get one message, and another when the service is back. HTTP answers below 500
count as alive, so a login page that says 401 is fine. Alerts follow the rules
`service_down` and `service_up`.

## Network analysis

Reads Pi-hole's statistics: queries over 24 hours, devices, most requested and most
blocked domains, DNS servers, and per device its recent domains.

What it cannot show: DNS only knows domain names, not pages or content. Devices that do
not use Pi-hole as DNS server, or that use encrypted DNS of their own, are missing.
This is data about what people in your home do online. It stays on the Pi and is for
admins only, but tell the people in your network that it exists.

## Smart home

Needs an MQTT broker (Settings › Integrations). Devices are found through Home Assistant
discovery, which Zigbee2MQTT, Tasmota and ESPHome offer, or added by hand with their
state and command topics. Anyone who can write to your broker can change what is shown
here, so protect it with a password.

## Remote commands

Runs a fixed command on another computer over SSH, for example shutting down a PC so that
its encrypted disk locks. There is no free-form input: only commands an admin set up in
the dashboard can run. Each run needs two confirmations by the same person within a
minute. In chat the safe answer moves to where "yes" was; in the dashboard the name has to
be typed. Every run is in the audit log.

Set up the other computer:

1. Turn on its SSH server (Windows: *Settings › Optional features › OpenSSH Server*).
2. Copy pi-guard's public key from the add-on page into `~/.ssh/authorized_keys`
   (Windows administrators: `C:\ProgramData\ssh\administrators_authorized_keys`).
3. Limit the key to this one command, so that even a stolen Pi can do nothing else:

   ```text
   command="sudo systemctl poweroff",no-port-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAA… pi-guard
   ```

4. Linux: allow that command without a password, in `/etc/sudoers.d/pi-guard`:

   ```text
   youruser ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff
   ```

The first connection remembers the computer's key; if it changes later, the command is
refused. Shutting down protects a disk only if it is encrypted and locks on shutdown.

## AI assistant

With the add-on on, anything you write that is no command is a question; voice messages
get an extra button, and the dashboard has a text box and a microphone.

| Provider | Text | Voice in | Voice out |
| --- | --- | --- | --- |
| Mistral | `mistral-small-latest` | yes | no |
| OpenAI | `gpt-4o-mini` | yes | yes |
| Own service | any OpenAI-compatible API | if offered | no |

Set your own model name if the default is outdated. API keys are entered on the add-on
page. A DeepL key is optional and handles requests like "Übersetze Guten Morgen ins
Englische".

How it saves tokens: weather (Open-Meteo, no key), time and date never reach the AI;
the same question within an hour comes from a cache; the prompt is one short sentence,
answers are capped, and only the last few turns are sent. When the daily token limit is
reached the AI pauses until the next day; weather and time keep working.

Privacy: questions go to the chosen provider. The assistant receives no camera pictures,
no names, no device or presence data and no secrets, and it cannot switch anything or
run commands.

## Your connection

The overview shows how your browser is connected: home network, home VPN with the
device's name, or from the internet. To tell whether you go through another VPN or Tor, the
browser asks `api.ipify.org` for its public address; the Pi compares that with your home
address and Tor's list of exit nodes, and asks `ipwho.is` who owns an unknown address.
"Probably another VPN" is an estimate based on the owner's name. Tor Browser usually
cannot reach a dashboard inside your home at all.
