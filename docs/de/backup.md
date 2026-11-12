# Backups

pi-guard sichert seine Konfiguration und Daten auf Wunsch selbst: in eine Nextcloud, per rsync auf einen Server oder in
ein Borg-Repository. Einrichten unter *Einstellungen → Daten & Sicherung → Backup*. Ausführlich: [backup.md](../backup.md).

- **Was drin ist:** `/etc/pi-guard` mit Konfiguration und `.env` (also auch Tokens und Passwörter), die Datenbanken,
  Gesichtsabdrücke, Schlüssel, Töne, die Signal-Verknüpfung. Aufnahmen und Bilder nur, wenn *Auch Aufnahmen und Bilder*
  an ist.
- **Verschlüsselt:** Ein Backup verlässt den Pi nur verschlüsselt, mit AES-256-GCM und einem Schlüssel aus deiner
  Backup-Passphrase (mindestens 12 Zeichen). Nextcloud oder der Server sehen nur die verschlüsselte Datei. Ohne die
  Passphrase kann niemand ein Backup wiederherstellen, auch du nicht: bewahre sie getrennt auf, etwa im
  Passwort-Manager.
- **Ziele:** Nextcloud (mit App-Passwort), rsync über SSH (mit dem SSH-Schlüssel des Pi, den *Schlüssel erzeugen*
  zeigt) oder Borg (verschlüsselt selbst, braucht `borgbackup`).
- **Wiederherstellen:** `sudo ./scripts/restore.sh DATEI` fragt nach der Passphrase, stoppt pi-guard, legt Vorhandenes
  als `*.before-restore-DATUM` daneben, setzt Konfiguration und Daten des Backups ein und startet pi-guard. Von Hand:
  `pi-guard backup restore DATEI --into ordner` entpackt nur in den Ordner.

## Umzug

Unter *Einstellungen → Daten & Sicherung → Umzug*, für ein neues Zuhause, einen neuen Router oder Internetanbieter, oder einen neuen Pi.

**Vorher:**

1. **Umzugsdatei herunterladen:** ein verschlüsseltes Backup direkt in den Browser, mit eigener Passphrase (mindestens 12
   Zeichen), auf Wunsch mit Aufnahmen. Auf einem neuen Pi: pi-guard installieren, Datei hinüberkopieren,
   `sudo ./scripts/restore.sh DATEI`.
2. **Netz des Pi:** Am einfachsten bekommt der neue Router denselben WLAN-Namen und dasselbe Passwort, dann verbinden
   sich Pi, Kameras und Handys von selbst. Sonst baut die Seite den `nmcli`-Befehl, der das neue WLAN zusätzlich
   einträgt; der Schlüssel bleibt im Browser, und den Befehl führst du selbst auf dem Pi aus. Am Kabel einfach am neuen
   Router einstecken.
3. **Was der alte Router tut**, notieren: feste Adresse des Pi, Pi als DNS-Server, weitergeleiteter VPN-Port,
   FRITZ!Box-Benutzer für pi-guard.

**Danach** merkt pi-guard, dass es in einem anderen Netz ist: ein anderer Router (an seiner Hardware-Adresse, denn jede
FRITZ!Box heißt 192.168.178.1), andere Adressen oder ein anderes WLAN. Die Prüfungen sagen, was zu tun ist:

- **Netzwerkkameras**, deren Adresse nicht mehr im Netz liegt oder die nicht antworten.
- **FRITZ!Box:** ob sie unter der eingetragenen Adresse antwortet, sonst Adresse und Benutzer neu eintragen.
- **Pi-hole als DNS-Server:** ob andere Geräte ihn fragen; sonst im neuen Router den Pi als lokalen DNS-Server eintragen.
- **VPN von unterwegs:** ob die eingetragene Adresse noch die des Zuhauses ist. *Übernehmen* setzt die neue (mit
  Passwort, wirkt nach dem Neustart); die Handy-App holt sie sich selbst, Computer und WireGuard-Apps brauchen ihren
  Zugang neu. Den UDP-Port im neuen Router weiterleiten, oder ohne öffentliche IPv4-Adresse Relay oder Tailscale nutzen.
- **Sperren für einzelne Geräte**, die über eine IP-Adresse gehen.

*Das ist jetzt mein Zuhause* speichert das neue Netz, damit der nächste Wechsel wieder auffällt. pi-guard ändert dabei
nie selbst die Netzwerkeinstellungen des Pi. Getestet mit nachgebauten Netzen, noch nicht bei einem echten Umzug.
