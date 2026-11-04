# Discord (optional)

A Discord bot does the same as the Telegram bot: commands, buttons, photos, videos and alerts. The
connection goes out from pi-guard to Discord, so no port has to be opened.

pi-guard talks to people in **direct messages only**. Messages in server channels are ignored, because
pictures from your home do not belong there. Pictures and alerts pass through Discord's servers.

## Create the bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and create a **New
   Application**.
2. Under **Bot**, press **Reset Token** and copy the token. Treat it like a password.
3. No privileged intents are needed.

## Find your user ID

In Discord, open *User Settings → Advanced* and switch on **Developer Mode**. Then right-click your name
and choose **Copy User ID**. If you write to the bot without being listed, it answers with your ID too.

## Configure pi-guard

In the dashboard under *Settings → Messenger → Discord*: enter the token, add your user ID as admin,
switch on Discord, save and restart. Or in the environment file:

```dotenv
PI_GUARD_DISCORD_ENABLED=true
PI_GUARD_DISCORD_BOT_TOKEN=...
PI_GUARD_DISCORD_ADMIN_USER_IDS=123456789012345678
PI_GUARD_DISCORD_VIEWER_USER_IDS=
PI_GUARD_EMERGENCY_DISCORD_USER_IDS=
```

Admins can use every command, viewers can watch, and both get alerts.

## Invite the bot once

Discord only lets a bot write to people who share a server with it. After the restart, press **Invite
bot to a server** under *Settings → Messenger* and add it to a server of yours, for example a private
one of your own. It needs no permissions there. Everyone listed must be on that server, and must allow
direct messages from its members.

Then press **Send test message**, or write `menu` to the bot. `pi-guard discord test` checks the token.

## Limits

- Bots may send files up to 10 MB. A larger video arrives as text only; shorten the clip.
- Photos and videos sent to the bot are only processed from admins.
