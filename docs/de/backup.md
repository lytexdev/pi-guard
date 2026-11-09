# Backups

pi-guard sichert seine Konfiguration und Daten auf Wunsch selbst: in eine Nextcloud, per rsync auf einen Server oder in
ein Borg-Repository. Einrichten unter *Einstellungen → Backup*. Ausführlich: [backup.md](../backup.md).

- **Was drin ist:** `/etc/pi-guard` mit Konfiguration und `.env` (also auch Tokens und Passwörter), die Datenbanken,
  Gesichtsabdrücke, Schlüssel, Töne, die Signal-Verknüpfung. Aufnahmen und Bilder nur, wenn *Auch Aufnahmen und Bilder*
  an ist.
- **Verschlüsselt:** Ein Backup verlässt den Pi nur verschlüsselt, mit AES-256-GCM und einem Schlüssel aus deiner
  Backup-Passphrase (mindestens 12 Zeichen). Nextcloud oder der Server sehen nur die verschlüsselte Datei. Ohne die
  Passphrase kann niemand ein Backup wiederherstellen, auch du nicht: bewahre sie getrennt auf, etwa im
  Passwort-Manager.
- **Ziele:** Nextcloud (mit App-Passwort), rsync über SSH (mit dem SSH-Schlüssel des Pi, den *Schlüssel erzeugen*
  zeigt) oder Borg (verschlüsselt selbst, braucht `borgbackup`).
- **Wiederherstellen:** `pi-guard backup restore DATEI --into ordner` fragt nach der Passphrase und entpackt nur in
  den Ordner. Danach pi-guard stoppen, `config/` nach `/etc/pi-guard` und `data/` nach `/var/lib/pi-guard` kopieren und
  wieder starten.
