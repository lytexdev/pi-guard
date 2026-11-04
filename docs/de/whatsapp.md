# WhatsApp

pi-guard nutzt die offizielle WhatsApp Cloud API von Meta. Inoffizielle Brücken verstoßen
gegen die Regeln von WhatsApp und können zur Sperrung der Nummer führen.

Die wichtigsten Grenzen:

- Sie brauchen ein Meta-Entwicklerkonto, eine WhatsApp-Business-App und eine Nummer, die
  nicht in einem normalen WhatsApp-Konto benutzt wird.
- **Empfangen braucht eine öffentliche HTTPS-Adresse**, die nur `/webhook/whatsapp`
  weiterleitet. Ohne sie kann pi-guard nur senden. Stellen Sie nie das ganze Dashboard ins
  Internet.
- **Freier Text geht nur innerhalb von 24 Stunden** nach der letzten Nachricht der Person.
  Spätere Alarme brauchen eine genehmigte Vorlage, die keine Details enthält.
- Befehle gehen nur als Text.

Für zuverlässige Alarme bleibt Telegram die bessere Wahl. Einrichtung:
[whatsapp.md](../whatsapp.md).
