"""Linked households: one pi-guard helps another without being in its network.

A *helper* (for example the person who set up a grandmother's Pi) lets other households
connect to it (`federation_listen`) and hands out a one-time code. The *helped* Pi enters the
code and from then on keeps one outgoing, encrypted connection to the helper. Nothing has to be
opened at the helped home: no port, no VPN, no login of the helper there.

Over that connection the helped Pi sends a short status and, if allowed, its alarms and SOS.
The helper can open the helped dashboard through it, with the rights the helped side granted
(viewer or admin, camera pictures and emergency data only when ticked). The helped Pi checks
every request itself and refuses anything that needs a password or would outlive the visit:
logins, keys, tokens, backups, exports and the links themselves.

Security: TLS to a certificate pinned in the code, and on every connection both sides prove
their Ed25519 key over fresh nonces. The code's token works once and for a day. Either side can
remove the link at any time.
"""
