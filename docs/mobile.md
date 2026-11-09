# Mobile app (Android)

PiGuard is the dashboard as an Android app, with what only a phone can do. It is optional: switch on
**Mobile app** under *Add-ons* and restart. The server side does not depend on Android, so an iOS app could
use the same add-on.

## Install

1. Under *Add-ons → Mobile app*, open the page on your phone in the home network and press **Download app**.
2. Open PiGuard while in the home network. It searches the network for pi-guard, or you type the address.
3. Check the certificate fingerprint and log in. The phone then has its own token and stays logged in.

New phones can only be connected from the home network or the VPN. Only admins get a VPN access: it reaches the whole
home network, and viewers may only watch. Remove a phone under *Add-ons → Mobile
app* and its token stops working at once.

## What it does

- **Dashboard** with all pages and settings, also from outside through the VPN or a public address.
- **Alerts** arrive over the app's own encrypted connection to pi-guard, with no Google or Apple service in
  between. A permanent notification keeps it alive, which uses some battery. Under each alert, *All fine* acknowledges
  all open alerts and *Sound alarm* sounds the alarm at home for 30 seconds; pi-guard does either only for an admin.
- **VPN:** the app is a WireGuard client with its own access per phone, like a VPN app with a device list. pi-guard
  creates the access when the phone is paired, so there is no QR code to scan. Tap the shield at the top of the app,
  then *Connect VPN*. It works from anywhere, shows the device, address, data and last contact, and comes back after
  a restart. Android runs one VPN at a time: connecting replaces the WireGuard app, and if that app is set to
  always-on, Android refuses and the app explains how to turn that off. PiGuard can be the always-on VPN itself.
  With several [ways home](network.md#ways-home-from-outside) set up, it tries them in turn and shows which one
  it uses. With Tailscale, it finds the dashboard at that address by itself.
  The first access needs the app opened once at home or through another VPN. WireGuard must be set up on pi-guard.
  Do this once at home. All traffic then goes through your home.
- **Find a phone:** from *Add-ons → Mobile app* or `/findphone name` in chat. It rings at full volume and
  vibrates. Location, map and details are only sent if the phone's owner switched on *Share location*. The phone
  shows that someone is looking for it. The Pi fetches map tiles from OpenStreetMap, and only when someone opens the map.
- **Place link:** *Place link* asks the phone for its place right now, without ringing, and shows a link with how exact
  it is (± metres), to open, copy or share. Indoors a phone may have no GPS fix; then nothing new comes. The link opens
  in the map chosen as *Map for places* (`PI_GUARD_MAP_PROVIDER`): OpenStreetMap, Google Maps, Apple Maps, Bing, HERE,
  or the map app of the device. That map's company learns the place only when someone opens the link.
- **VPN options:** on the VPN screen, *Apps past the VPN* is split tunneling, *Connect automatically* connects whenever
  pi-guard cannot be reached directly. Kill switch and lockdown are Android's own always-on VPN with *Block connections
  without VPN*, which is the most reliable place for them: the system itself blocks everything past the tunnel.
- **Updates:** come from your pi-guard once a day on Wi-Fi; the pi-guard gets new signed versions from GitHub by itself. The first one asks for a tap, after that the app updates
  itself on Android 12 and newer. See [updates.md](updates.md).
- **Name and icon:** *App settings → App name and icon* turns the app into Calculator, Notes, Weather, Clock, Files or
  Music, with a matching icon, for example to keep it inconspicuous. Android fixes these names when the app is built,
  so for any other name and picture create your own shortcut on the home screen. The app cannot be hidden completely:
  since Android 10 every app with permissions shows up in the app list. Under the chosen look it does so inconspicuously.
- **Calendar:** busy entries of the phone's calendar count as away. Other calendars such as Google or Proton are
  connected in the dashboard under Settings › At home & away, also without the app.
- **SOS:** one tap starts a 10-second countdown; tapping again cancels. Then all emergency contacts and admins
  get a message with location, mode, battery and network, and a current picture of each camera. **This does not replace 112.** It needs a connection
  to pi-guard, and pi-guard must be running.
  - For 30 minutes after an SOS the phone sends its place every minute, and admins follow it under *Add-ons → Mobile app
    → Places*. *I am safe*, on the emergency screen or in the notification, ends it and tells everyone; the places are
    then deleted.
  - With *App settings → Photos and sound after an SOS* (off by default), both cameras take a photo and the
    microphone records 20 seconds while the emergency screen is open. They go to the emergency contacts and admins
    and are not kept on the phone or the Pi. Sound arrives through Telegram and WhatsApp; other messengers get the
    words only. Android lets an app use camera and microphone only while it is in front, so nothing is taken in the
    background.
  - *Record the microphone at home after an SOS* in the dashboard's phone settings (off by default) adds 20 seconds
    from the Pi's microphone. It needs microphone recording and plays the same chime as listening live.
- **Lost phone:** *Report lost* on the phone's card asks for your password and an optional text for its screen. The
  phone then shows that it is reported lost and sends its place every minute until someone taps *Found*, which
  deletes the places. Only the dashboard ends this, not the phone: whoever holds it may not be its owner. The
  places come only if *Share location* is on, so switch it on before a phone goes missing. Places are kept for at
  most 7 days.
- **Call:** on the Live page, *Call* streams your microphone to the speaker while you hear the camera. It also
  works in a normal browser. Expect a delay of a second or two.
- **Calendar:** with *Away during calendar entries*, busy entries for the next 14 days count as away, even if
  a phone is at home. Only start and end go to pi-guard; titles stay on the phone. All-day entries are left out.
- **App lock:** PIN and fingerprint or face, after 30 seconds in the background. The PIN is typed on a number pad that
  shows dots, never digits, and it is set by typing it twice.

On a phone the dashboard's settings are a list: tap a section to open it, and the arrow at the top leads back.

The app asks for the camera only when you take pictures of a trusted person in the dashboard.

## Building and signing

```bash
./scripts/build-android.sh 1.0.0                  # signed APK
./scripts/build-android.sh 1.0.0 --publish system # also put it on an ssh host running pi-guard
```

This runs on a computer with the Android SDK, not on the Pi. The first run creates the signing key in
`~/.config/pi-guard/android`. **Back it up.** Android only accepts updates signed with the same key: without
it, every phone has to uninstall and connect again. It must never enter the repository.

A new version can also be uploaded under *Add-ons → Mobile app*. Phones look once a day and offer it. Android
always asks before installing an app that does not come from a store, so updates cannot be fully silent.

## Security

- The token is encrypted with a key in the Android Keystore. On pi-guard only its hash is stored.
- It replaces the password and second factor in the app, so a stolen unlocked phone is logged in. Use the
  app lock, and remove lost phones: that ends its login and its VPN access at once.
- The certificate is pinned at the first start. Anything not issued by your pi-guard, or by a public authority,
  is refused.

## Helping the project

At setup and later in the app settings, *Help make pi-guard better* offers two switches, both off until you turn them
on:

- **Crash reports:** after a crash the app keeps the exception's type and the places in its code, never the message,
  which can hold addresses or names, nor anything you saw or typed. On the next start it goes to your pi-guard if you
  agreed, and is deleted either way.
- **Usage data:** this app's and Android's version.

Both go only to your own pi-guard. It passes them on only inside its anonymous weekly report, and only if its owner
switched that on as well, see [privacy.md](privacy.md#anonymous-statistics).

## Using the app with your own pi-guard

The app works with any pi-guard: it finds it in the home network or takes its address, and trusts only that
pi-guard's own certificate authority after you compared its fingerprint.

| Way | How it updates |
|---|---|
| The official APK | From the GitHub releases (`apps-*`), signed. Your pi-guard fetches new versions by itself and offers them to the phone. |
| Uploaded to your pi-guard | Dashboard → *Add-ons → Phone app*; your pi-guard hands it to its phones. |
| Built from the source | `./scripts/build-android.sh VERSION` with your own keystore. Android refuses updates signed with another key, so switch off *Fetch app updates from GitHub* (`PI_GUARD_APP_UPDATES_FROM_GITHUB=false`) and publish your builds with `--publish HOST`. |
