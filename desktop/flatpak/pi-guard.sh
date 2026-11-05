#!/bin/sh
# zypak gives Chromium's sandbox what it needs inside Flatpak's own.
exec zypak-wrapper /app/lib/pi-guard/pi-guard-desktop "$@"
