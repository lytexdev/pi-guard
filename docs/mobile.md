# Mobile app (Android)

PiGuard is the dashboard as an Android app, with what only a phone can do. It is optional: switch on
**Mobile app** under *Add-ons* and restart. The server side does not depend on Android, so an iOS app could
use the same add-on.

## Install

1. Under *Add-ons → Mobile app*, open the page on your phone in the home network and press **Download app**.
2. Open PiGuard while in the home network. It searches the network for pi-guard, or you type the address.
3. Check the certificate fingerprint and log in. The phone then has its own token and stays logged in.

New phones can only be connected from the home network or the VPN. Remove a phone under *Add-ons → Mobile
app* and its token stops working at once.

## What it does

- **Dashboard** with all pages and settings, also from outside through the VPN or a public address.
- **Alerts** arrive over the app's own encrypted connection to pi-guard, with no Google or Apple service in
  between. A permanent notification keeps it alive, which uses some battery.
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
- **Calendar:** busy entries of the phone's calendar count as away. Other calendars such as Google or Proton are
  connected in the dashboard under Settings › At home & away, also without the app.
- **SOS:** one tap starts a 10-second countdown; tapping again cancels. Then all emergency contacts and admins
  get a message with location, mode, battery and network, and a current picture of each camera. **This does not replace 112.** It needs a connection
  to pi-guard, and pi-guard must be running.
- **Call:** on the Live page, *Call* streams your microphone to the speaker while you hear the camera. It also
  works in a normal browser. Expect a delay of a second or two.
- **Calendar:** with *Away during calendar entries*, busy entries for the next 14 days count as away, even if
  a phone is at home. Only start and end go to pi-guard; titles stay on the phone. All-day entries are left out.
- **App lock:** PIN and fingerprint or face, after 30 seconds in the background.

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
