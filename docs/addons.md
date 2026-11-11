# Add-ons

Optional extras, off by default. Choose them in the setup assistant or under
**Add-ons** in the dashboard; each needs a restart of pi-guard once. They are stored
under `addons:` in the configuration. Everything works in the dashboard and in every
messenger, where buttons become a numbered menu if the platform has none.

| Add-on | Command | Aliases |
| --- | --- | --- |
| Service watcher | `/services` | `/dienste` |
| Network management | `/netstats [ip]` | `/analyse` |
| Smart home | `/smarthome [name] [on\|off]` | `/smart`, `/schalte` |
| Remote commands | `/action` | `/aktion` |
| AI assistant | `/ask question`, `/weather [place]` | `/frage`, `/wetter` |

All but the assistant are for admins.

## Service watcher

Part of the *Server* add-on, see [agent.md](agent.md).

Watches websites (HTTP), ports (TCP) and hosts (ping). After several failed checks in a
row you get one message, and another when the service is back. HTTP answers below 500
count as alive, so a login page that says 401 is fine. Alerts follow the rules
`service_down` and `service_up`.

## Network management

Everything about the home network under **Network**: Pi-hole, Wi-Fi networks, devices, blocked
websites, block lists, VPN access, the map and the analysis.

**Wi-Fi and device names:** with the FRITZ!Box password set, the router lists its Wi-Fi networks
including the guest network, and the names it knows ("Galaxy-von-Ben"), read-only. You can
give each device your own name and owner. The domains a device asks for help to tell
devices apart, e.g. Apple, Roblox or Instagram.

**Blocking websites:** for everyone or one device, for good or for a while. Temporary blocks end
by themselves. A device with a private Wi-Fi address may change its IP, and then a block
for that device no longer fits.

**Block page:** without it, browsers just say a blocked site cannot be reached.
`sudo ./scripts/blockpage.sh setup 192.168.178.110` (a free address outside the DHCP range) gives
blocked sites a page of their own. Nearly all websites use HTTPS, so a browser only shows the
page if pi-guard's certificate is installed on the device; otherwise it shows its own warning.
Apps do not show pages at all, they simply fail. The script also allows Pi-hole's app
password to change settings.

Reads Pi-hole's statistics: queries over 24 hours, devices, most requested and most
blocked domains, DNS servers, and per device its recent domains.

What it cannot show: DNS only knows domain names, not pages or content. Devices that do
not use Pi-hole as DNS server, or that use encrypted DNS of their own, are missing.
This is data about what people in your home do online. It stays on the Pi and is for
admins only, but tell the people in your network that it exists.

## Smart home

Needs an MQTT broker. `sudo ./scripts/mqtt.sh setup` installs Mosquitto with a password and
connects pi-guard; `sudo ./scripts/mqtt.sh user NAME` adds a login per device. An existing
broker goes under Settings › Integrations. Devices are found through Home Assistant
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

The round button at the bottom right of the dashboard opens a chat with text and microphone.
With the add-on on, anything you write that is no command is a question; voice messages
get an extra button, and the dashboard has a text box and a microphone.
Questions about pi-guard itself ("How does the VPN work?", "Tutorial") are answered from the built-in tutorial,
without the provider and without cost; see [Help and tutorial](dashboard.md#help-and-tutorial).

| Provider | Default model | Voice in | Voice out |
| --- | --- | --- | --- |
| Mistral | `mistral-small-latest` | yes | no |
| OpenAI | `gpt-4o-mini` | yes | yes |
| Claude (Anthropic) | `claude-haiku-5-5` | no | no |
| Gemini (Google) | `gemini-2.5-flash` | no | no |
| Grok (xAI) | `grok-3-mini` | no | no |
| Ollama (local) | `llama3.2`, no key needed | no | no |
| Own service | any OpenAI-compatible API | if offered | no |

Ollama can run on a stronger computer in your network: enter its address, e.g.
`http://192.168.178.20:11434/v1`. A Raspberry Pi itself is slow for language models.

Set your own model name if the default is outdated. API keys are entered on the add-on
page. A DeepL key is optional and handles requests like "Übersetze Guten Morgen ins
Englische".

How it saves tokens: weather (Open-Meteo, no key), time and date never reach the AI;
the same question within an hour comes from a cache; the prompt is one short sentence,
answers are capped, and only the last few turns are sent. When the daily token limit is
reached the AI pauses until the next day; weather and time keep working.

Privacy: questions go to the chosen provider. By default the assistant receives no camera
pictures, no names, no device or presence data and no secrets. Each of those needs its own
tick below.

### Doing things by asking

Admins can ask for things in the chat widget, Telegram, Signal and WhatsApp:

- "Licht im Schlafzimmer aus", "Fahre meinen PC herunter", "Spiel den Alarm ab"
- "Nimm 10 Minuten Video auf", "Erstelle einen VPN-Zugang für Anna"
- "Nimm heute Nacht von 3 bis 4 Uhr Ton auf" (planned, up to two hours, then sent to you)
- "Wie lange geht noch die Spülmaschine?" reads a sensor, if there is one with that name

**Nothing happens without your yes**, and remote commands need a second one. Common sentences
are understood right here, without AI and without tokens. For free wording, tick "The AI may
plan actions": names of devices and cameras then go to the provider. The AI only picks from a
fixed list of actions; everything it returns is checked, and it never sees documents while
planning. It cannot invent commands.

Device state only exists if the device reports it over MQTT, so the dishwasher question needs a
dishwasher that does.

Settings are explained and changed under *Settings → General → Ask about settings*, always with a confirmation and an
undo, see [the dashboard page](dashboard.md#asking-about-settings-and-undoing-changes).

### Looking at cameras

With "The AI may look at camera pictures" ticked, "Was siehst du im Wohnzimmer?" sends one
picture to the provider after you confirm, each time. It needs a model that can see, e.g. a
vision model in Ollama for keeping pictures at home. The provider then has a picture of the
inside of your home, so use this with care.

### Your own files and documents

Upload TXT, MD, JSON or PDF on the add-on page. Each file is checked (type, size, valid
JSON, readable PDF), turned into plain text and split into small passages. A question only
carries the three or so passages that match. Scanned PDFs without a text layer cannot be
read.

Paperless-ngx (URL and API token) and Nextcloud (URL, user and app password) can be
connected too, so "What is my tax ID?" can be answered from a document. Answers name
their source. Repeated questions come from a store until files change, at no cost.

Only admins get answers from documents, in private chats and the dashboard. A cloud
provider receives excerpts only if you tick "Excerpts may go to the cloud provider": a tax ID
then leaves your home. With Ollama nothing leaves the network, so that is the right
choice for sensitive documents. Language models can be wrong: check important numbers
in the source document.

## Your connection

The overview shows how your browser is connected: home network, home VPN with the
device's name, or from the internet. To tell whether you go through another VPN or Tor, the
browser asks `api.ipify.org` for its public address; the Pi compares that with your home
address and Tor's list of exit nodes, and asks `ipwho.is` who owns an unknown address.
"Probably another VPN" is an estimate based on the owner's name. Tor Browser usually
cannot reach a dashboard inside your home at all.

## Slack, Matrix, Rocket.Chat, Mastodon

Alerts as cards in other services, with a title and a colour by urgency. Switch it on under Add-ons and fill in what you
use under Settings › Messenger:

- **Slack and Rocket.Chat:** the address of an incoming webhook. It works like a password.
- **Matrix:** server, room ID and an access token. Use a room without encryption, the bot writes plainly.
- **Mastodon:** server and an access token with the right to post. Alerts go out as direct messages, never publicly.

These are send-only and carry no pictures: what your cameras see does not belong in a team chat. Under Settings ›
Notifications you choose per kind of message whether it goes there.

## Kid Protect

Filters for children's devices, and times without internet. Needs Pi-hole for the filters; times without internet
also work with a FRITZ!Box alone. Open it under *Add-ons → Kid Protect*.

- **Profiles:** one per child, or one for all children. A profile blocks kinds of sites: adult content (18+),
  gambling, malware and phishing, scams, drugs, piracy and torrents, social networks, TikTok. The first five are ticked
  for a new profile. The lists come from [The Block List Project](https://blocklistproject.github.io/Lists/) and
  StevenBlack's social list; Pi-hole downloads them.
- **Times without internet:** for example 21:00 to 06:30 on school nights, a weekend night or homework time, with
  templates for these. A time past midnight belongs to the day it starts. *Pause internet* switches it off now for 15
  minutes to two hours, *Extra time* gives internet during a time without it.
- **Devices:** choose a profile for each child's device in the list of the home network; devices without a profile are
  not changed. Phones with a private (changing) Wi-Fi address need it switched off for the home network, or the
  profile does not recognise them.

How it works: each profile is a Pi-hole group (`pi-guard-kids-N`), its devices are Pi-hole clients by MAC address in
that group and in the default group, and the profile's lists are assigned to the group only, so nobody else is
filtered by them. A list that was already there for everyone stays so. A time without internet adds one rule that
blocks every name for the groups that are offline; with a FRITZ!Box the router also cuts these devices' internet, and
only what Kid Protect cut is given back afterwards. The lists made by Kid Protect are not shown under *Network → Lists*.
Every change goes to Pi-hole at once, and the add-on checks every minute.

DNS filters are no lock: mobile data, a VPN or another DNS server get around them. They help younger children most,
together with talking about it. Tested with a stand-in for Pi-hole's API, not yet against a real Pi-hole and router.

## Reverse proxy

Reach services behind NAT through a server you choose, like frp: this Pi as server or client of `pgproxy`, with
tokens, allowed ports and a pinned certificate. See [proxy.md](proxy.md).

## Removing an add-on

*Remove* on an add-on's card switches it off and deletes its settings and data, and shows how much space that frees.
The assistant loses its stored knowledge, the language add-on its translations. Installing it again starts from nothing.

## Costs of the assistant

The assistant page shows tokens per day and month. Enter the prices of your model from your provider's price list to see
them in euros or dollars, and your budget to see what is left. Providers do not tell a normal API key its balance, so
this is counted by pi-guard and leaves out speech; your provider's bill is what counts.

## Emergency data

Medical information of the people at home, for when help comes: blood type, allergies, illnesses and diagnoses,
medicines and the medication plan, over-the-counter products and supplements, implants such as a pacemaker, organ
donation, people to call, doctor and health insurance, and the person's phone and email. All but the name are optional.

- **Medicines** are lines as on the German federal medication plan (*Bundeseinheitlicher Medikationsplan*, BMP):
  name, strength, active ingredient, form, doses morning-noon-evening-night or a free schedule, unit, reason and a
  note, regular or as needed, and whether a doctor prescribed it or the person takes it on their own. Products bought
  without prescription (vitamins, minerals, herbal products) have a section of their own.
- **Read medication plan (BMP):** scan the Data Matrix code of a printed plan with a scanner app and paste the text,
  or choose the file. The medicines fill the form; nothing is saved before *Save*. *BMP* gives the medicines back as
  such a text. Drawing the Data Matrix code itself is not done. This follows the published structure of the plan;
  plans from a real practice or pharmacy have not been tried yet.
- **Organ donation** is a choice (yes, no, only some organs, all but some, a person decides, not decided) with a note.
- **Markdown** works in the text fields: `**bold**`, `*italic*`, lists with `-`, headings with `#`. *Preview* shows it;
  the rescuer's page and the PDF show it too.
- **Dashboard account:** a person can be linked to one account. An SOS from that account's phone then sends the
  emergency contacts that person's data, if they are set to get them. Removing the account ends the link.
- **PDF, picture, send:** an open sheet can be saved as a PDF (A4, to print), as a picture (PNG, drawn in the browser,
  to share from the phone) and sent to an emergency contact. Within one open sheet the password typed to open it is
  used for these and forgotten when the sheet closes.

Health data are a special category under Art. 9 GDPR, so:

- Each record is encrypted with AES-256-GCM in its own database (`DATA_DIR/medical.db`) with its own key
  (`DATA_DIR/medical.key`), both readable by the service only. Deleted records are overwritten. This protects a copied
  file; the running Pi can read them, as it must to show them.
- Only admins see, change or share them, and each time with their password. Viewers never see them.
- Every look, change, link, opened link and wrong PIN goes into the audit log, with the record's number, never its
  content.
- Nothing of it goes into push messages or MQTT. It reaches a chat only when an admin sends it to an emergency contact,
  or an SOS does for a contact set to get it: as a rescue link valid an hour with its PIN in a second message, or as
  the PDF if the owner chose that for this contact (the chat service then keeps the file). It is part of the data
  export.

**Rescue link:** *Rescue link* makes a link to one person's sheet, valid for 15, 30 or 60 minutes, read only. By
default it also asks for a six-digit PIN: tell it by phone, not in the same message as the link. Five wrong PINs end
the link, and *End now* ends it at once. The page puts what can harm the person first (allergies, illnesses,
medicines, implants), works without a login and loads nothing from elsewhere, and can be printed. Only hashes of
link and PIN are stored.

Ask the people you add: these are their data.
