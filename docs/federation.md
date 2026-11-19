# Linked households

One pi-guard can look after another: a grandmother's, a parent's, a second home. The helper sees the household's
state, gets its alarms and every SOS, and opens its dashboard, without being in its Wi-Fi or VPN. Nothing has to be
opened at the helped home. German: [Verbundene Haushalte](de/haushalte.md).

```text
Helped Pi (grandmother)  ──── one outgoing, encrypted connection ────►  Helper's Pi (you)
     no open port                                                      port 8444 forwarded
```

Everything is off until switched on (`federation_enabled`). Each link has a direction: *helper* and *helped*. Two
households that look after each other make two links.

## Setting it up

On the **helper's** Pi (once):

1. *Settings → Linked households*: switch on *Use linked households* and *Other households may connect here*, and
   restart pi-guard.
2. In the router, forward **TCP port 8444** (`federation_port`) to this Pi. Only the link answers there, with its own
   certificate; the dashboard is never reachable on it. *Public address* (`federation_address`) is how the other Pi
   reaches yours: a DynDNS name, or empty for the public name of the VPN (`wireguard_endpoint`). The Pi-hole block
   page (`blockpage_ip`, [network](network.md)) uses 8088 and 8444 itself: with it, choose another port such as 8445.
   The settings refuse a port the dashboard or the block page uses, and the page and *System → Health* say when the
   port is taken.
3. *Make a code* (with your password). The code works **once and for 24 hours**. Send it a safe way, for example by
   Signal, or type it in yourself at the other home.

On the **helped** Pi (for example while setting it up at the grandmother's):

1. Switch on *Use linked households* (not the listening switch) and restart pi-guard.
2. *Enter a code*, paste it, and choose what the helper may do:
   - *Change everything (admin)* or *Only look*;
   - *Alarms and state*: alarms as messages to the helper's admins (an SOS always arrives);
   - *Camera pictures and recordings*: without the tick, pictures, recordings and faces stay in the household;
   - *Emergency data*: the emergency data add-on.
3. Confirm with the password. The Pi connects at once and from then on by itself, also after a restart.

Both pages show a short key (`dd26 7660 af33 5c9a`). Comparing them on the phone shows that nobody is in between.

## Using it

The helper's page lists each household with its state: online, home or away, cameras running, open alerts, the last
event, version. *Open dashboard* opens the household's dashboard in a new tab, with a banner that says whose it is and
the way back. It works like being there, with the rights the household granted. Alarms and SOS from the household come
as messages to the helper's admins, at most one per household and kind each minute.

Never through the link, whatever was granted: logins, passkeys, key files, API tokens, the accounts, backups, exports,
updates for devices, live video and sound, and the links themselves. Whatever needs the password works only at home: the
helper never has it. The helped side can change the rights or remove the link at any time; the helper can remove it too.
Removing ends an open connection at once.

## Households as contacts

A linked household is also a way to reach someone, like a messenger: in a contact's ways, *Linked household* picks
one of the links. Such a contact can have any role: second line, emergency contact, gets alerts, SOS, trusted person.
*As a contact* on a link opens the contact form with the household already filled in. This works on both sides of a
link: the helper can name the helped household, and the helped household its helper.

What goes through: the words of the message only. They reach the other household's admins on their messengers, under
the household's name; an urgent message (an alarm, an escalation, an SOS) arrives there like an SOS, on every
messenger. Pictures, sound, files and the emergency data sheet stay home. A household that sends more than 20 messages
in 10 minutes is cut off until it calms down. A household that is not connected right now is skipped like a messenger
that is down. Invitations with login details never go through a link: they would reach the other household's admins
instead of the person.

## How it is protected

- The helped Pi opens the connection: TLS to a certificate that is **pinned** in the code (no certificate authority
  involved), on a port that serves nothing else.
- On every connection both Pis prove their **Ed25519** key over fresh nonces, bound to their role. The helped Pi only
  talks to the key from the code; the helper only accepts keys it linked. Keys are made once and stored sealed with the
  dashboard's secret key.
- The code's token is kept only as a hash and works once; failed attempts from one address are limited.
- A helper's request is checked twice on the helped Pi: by the link's rules, then by the dashboard like any request,
  as the user `fed:<id>` with the granted role, and written to its audit log.
- The helper's browser runs only its **own** pi-guard page: a service worker for `/fed/<id>/` sends the page's API
  requests through the link, and the answers are only data. Pages, scripts or SVG from a helped Pi are never shown on
  the helper's address, so a broken-into household cannot reach the helper's Pi through the browser.

## Limits

- The helper's Pi must be reachable from outside on its port (port forwarding, or an address with IPv6). The helped Pi
  needs none.
- The helped dashboard uses the helper's page version. Both should run the same release; signed updates keep them
  close.
- Live video does not pass the link; with *Camera pictures* the helper sees snapshots and recordings.
- Tested in a process with two pi-guards, a real TLS link and a browser (Chromium). Not yet tested between two real
  homes.
