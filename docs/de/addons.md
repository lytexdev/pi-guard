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

**Dienste-Wächter:** Prüft Webseiten, Ports und Geräte. Nach mehreren Fehlversuchen kommt
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

Details: [addons.md](../addons.md).
