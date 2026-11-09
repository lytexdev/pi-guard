# Add-ons

Optionale Erweiterungen, ab Werk ausgeschaltet. Sie werden im Einrichtungsassistenten
oder im Dashboard unter **Add-ons** gewählt und brauchen einmal einen Neustart von
pi-guard. Alles funktioniert im Dashboard und in jedem Messenger.

| Add-on | Befehl |
| --- | --- |
| Dienste-Wächter | `/dienste` |
| Netzwerkanalyse | `/analyse [IP]` |
| Smart Home | `/smart [Name] [an\|aus]` |
| Fernbefehle | `/aktion` |
| KI-Assistent | `/frage Text`, `/wetter [Ort]` |

**Dienste-Wächter** (Teil des Add-ons *Server*, zusammen mit den Computern mit Agent): Prüft Webseiten, Ports und Geräte. Nach mehreren Fehlversuchen kommt
eine Nachricht, und noch eine, wenn der Dienst wieder läuft.

**Netzwerkanalyse:** Zeigt aus den Daten von Pi-hole Geräte, meistaufgerufene und
geblockte Domains. Sichtbar sind nur Domains, keine einzelnen Seiten, und nur Geräte, die
Pi-hole als DNS nutzen. Das sind Daten darüber, was Menschen in Ihrem Haushalt im
Internet tun: Sie bleiben auf dem Pi und sind nur für Admins sichtbar, aber sagen Sie den
Menschen in Ihrem Netz bitte, dass es diese Auswertung gibt.

**Smart Home:** Braucht einen MQTT-Broker. Geräte werden über die Home-Assistant-Erkennung
gefunden (Zigbee2MQTT, Tasmota, ESPHome) oder von Hand eingetragen.

**Fernbefehle:** Führt feste Befehle per SSH auf einem anderen Computer aus, zum Beispiel
„PC herunterfahren“, damit die verschlüsselte Festplatte gesperrt ist. Freie Eingaben
gibt es nicht. Jeder Lauf braucht zwei Bestätigungen derselben Person innerhalb einer
Minute. Den Schlüssel von pi-guard tragen Sie auf dem anderen Computer in
`authorized_keys` ein, am besten beschränkt auf genau einen Befehl
(siehe [addons.md](../addons.md#remote-commands)). Herunterfahren schützt die Festplatte
nur, wenn sie verschlüsselt ist.

**KI-Assistent:** Alles, was kein Befehl ist, gilt als Frage. Wetter, Uhrzeit und Datum
kosten keine Tokens, gleiche Fragen kommen eine Stunde lang aus dem Zwischenspeicher, und
ein Tageslimit begrenzt die Kosten. Fragen gehen an den gewählten Anbieter. Der Assistent
bekommt keine Bilder, Namen oder Daten aus Ihrem Zuhause und kann nichts schalten.

**Eigene Dateien und Dokumente:** Der Assistent kann TXT, MD, JSON und PDF sowie
Paperless-ngx und Nextcloud durchsuchen, etwa nach Ihrer Steuer-ID. Das gilt nur für Admins.
Ausschnitte gehen nur dann an einen Cloud-Anbieter, wenn Sie das ausdrücklich erlauben; die
Steuer-ID verlässt dann Ihr Zuhause. Mit Ollama bleibt alles im Heimnetz. Sprachmodelle
können sich irren: Prüfen Sie wichtige Zahlen im Dokument.

**Netzwerk-Verwaltung:** Unter Netzwerk finden Sie WLANs (auch Gast-WLAN), Geräte mit Namen aus
dem Router, gesperrte Webseiten, Sperrlisten, VPN und Karte. Eine eigene Sperrseite richtet
`sudo ./scripts/blockpage.sh setup <freie-IP>` ein. Bei HTTPS erscheint sie nur auf Geräten mit
installiertem pi-guard-Zertifikat, sonst zeigt der Browser eine eigene Warnung.

**Sprachbefehle:** „Licht im Schlafzimmer aus“, „Fahre meinen PC herunter“, „Nimm heute Nacht von 3
bis 4 Uhr Ton auf“. Vor jeder Aktion fragt pi-guard nach, Fernbefehle zweimal. Häufige Sätze
versteht es ohne KI.

**MQTT selbst bereitstellen:** `sudo ./scripts/mqtt.sh setup` installiert Mosquitto mit Passwort.

Details: [addons.md](../addons.md).

**Notfalldaten:** Medizinische Angaben der Menschen im Haus für den Notfall: Blutgruppe, Allergien, Erkrankungen,
Medikamente und Medikamentenplan, Implantate wie ein Herzschrittmacher, Organspendeausweis, anzurufende Personen,
Hausarzt und Krankenkasse. Bis auf den Namen ist alles freiwillig.

- Verschlüsselt (AES-256-GCM) in einer eigenen Datenbank mit eigenem Schlüssel, nur für den Dienst lesbar; Gelöschtes
  wird überschrieben.
- Nur Admins sehen, ändern oder teilen sie, und jedes Mal mit ihrem Passwort. Zuschauer sehen sie nie.
- Jeder Blick, jede Änderung, jeder Link, jedes Öffnen und jede falsche PIN steht im Protokoll, mit der Nummer des
  Eintrags, nie mit dem Inhalt.
- Nichts davon geht in Chats, Push-Nachrichten oder MQTT. Es ist Teil des Datenexports.

**Notfall-Link:** zeigt das Blatt einer Person 15, 30 oder 60 Minuten lang, nur lesend, ohne Anmeldung. Standardmäßig
zusätzlich mit sechsstelliger PIN: Sag sie am Telefon, nicht in derselben Nachricht wie den Link. Fünf falsche PINs
beenden den Link, *Jetzt beenden* sofort. Was der Person schaden kann, steht ganz oben; die Seite lässt sich drucken.

Frag die Menschen, die du einträgst: Es sind ihre Daten.
