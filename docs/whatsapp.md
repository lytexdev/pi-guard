# WhatsApp

pi-guard uses Meta's official WhatsApp Cloud API. Unofficial bridges that imitate
WhatsApp Web are against WhatsApp's terms and can get a number banned, so they are not
supported.

Be aware of the limits before you start:

- You need a Meta developer account, a WhatsApp Business app and a phone number that is
  not in use in a normal WhatsApp account.
- **Receiving needs a public HTTPS address.** Meta calls `POST /webhook/whatsapp` over the
  internet with a certificate it trusts. A home VPN does not help here. Without it,
  pi-guard can send to WhatsApp, but commands sent to it do not arrive.
- **Free text is only allowed within 24 hours of a person's last message.** Later alerts
  need an approved message template (`PI_GUARD_WHATSAPP_ALERT_TEMPLATE`), which arrives
  without the alert's details. Sending can cost money; see Meta's pricing.
- Commands are text only; pictures or voice sent to pi-guard are ignored. Replies use a
  numbered menu, like Signal. Emergency contacts remain Telegram and Signal.

For dependable alerts, Telegram stays the better choice.

## Setup

1. Create an app at <https://developers.facebook.com> and add the WhatsApp product.
2. Note the phone number ID, a permanent access token and the app secret.
3. Enter them in the dashboard under Settings › WhatsApp, with a verify token of your
   choice and your own number in international format, and restart.
4. Make only the webhook reachable from outside, for example with a Cloudflare Tunnel or a
   reverse proxy that forwards `/webhook/whatsapp` and nothing else. **Do not expose the
   whole dashboard.**
5. In the Meta app, set the callback URL to `https://your-name/webhook/whatsapp`, enter the
   verify token and subscribe to `messages`.

Every webhook call is checked against the app secret, and messages from numbers
not on your list are refused like in the other messengers.

This integration is tested against Meta's documented formats only, not against a live
WhatsApp account.
