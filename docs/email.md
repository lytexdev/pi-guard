# Email (optional)

Sends alerts and data exports by email. It is off by default and send-only: commands stay with the chat
messengers.

Switch on **Email** under *Add-ons*, then enter the mail server under *Settings → Messenger*. Every
recipient gets the alerts that go to all users. *Send test mail* checks the connection.

Mail reaches your server encrypted (STARTTLS or TLS; pi-guard refuses plain connections). After that it is
not end-to-end encrypted, and an alert photo lies in a mailbox. That is why this is an add-on.

## Exports

*Send export now* mails all stored data as a JSON file, the same as *Data & backup*, without keys, tokens or
face data. *Send data export* can also do this every week or month. The file holds events, logs and names.
