# Security

## Reporting a vulnerability

Please do not open a public issue for security problems. Use GitHub's
"Report a vulnerability" (private security advisory) on this repository.
Include steps to reproduce and the affected version. You will get an answer
within a week.

## Supported versions

Only the latest release receives security fixes.

## Security model

- **No inbound network access.** pi-guard opens no listening ports. Telegram
  uses outbound long polling; signal-cli listens on localhost only; MQTT is an
  outbound client connection.
- **Authorisation** happens centrally in the messenger for every provider:
  configured user IDs or single-use, expiring invitation codes; group chats
  must be allow-listed; per-user rate limiting; admin-only commands checked by
  role. Unknown senders are answered at most once per hour.
- **No command execution.** Chat commands map to fixed handlers. External
  programs (FFmpeg, ffprobe, ip) are started with argument lists, never through
  a shell. Uploaded audio is checked by file signature and parsed by ffprobe
  restricted to local files before it can be played.
- **Least privilege.** The service runs as a dedicated user with a hardened
  systemd unit (`ProtectSystem=strict`, no capabilities, `NoNewPrivileges`);
  the container runs read-only, unprivileged and without the Docker socket.
- **Secrets** (bot token, MQTT and router passwords) are only read from the
  environment file (`0640 root:pi-guard`), are masked in `config show`, and are
  redacted from logs, including tracebacks.
- **Data at rest** is readable only by the service user. Face embeddings are
  kept in a separate database and deleted with `secure_delete`.
- **MQTT commands** are off by default and limited to setting the mode;
  restrict the topic with broker ACLs if you enable them.

## Known limitations

- Telegram bot chats are not end-to-end encrypted.
- Anyone with physical access to the device or its SD card can read the
  recordings; pi-guard does not encrypt storage. Use full-disk encryption if
  this matters for you.
- signal-cli is an unofficial client maintained outside this project.
