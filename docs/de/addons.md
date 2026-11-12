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

Jede Karte hat einen Schalter und ein Menü (drei Punkte): *An Navigation anheften*, *Hilfe & Anleitung* (was das Add-on
macht und wie man anfängt, mit der Tour oder seiner Anleitung) und *Entfernen*. Telegram, Signal, WhatsApp, Discord,
E-Mail und die Chatdienste sind ebenfalls Add-ons; die Handy-App nicht: Sie gehört zu pi-guard, ist standardmäßig an und
liegt unter **Einstellungen › Apps**. Add-ons von anderen erscheinen als gleiche Karten unter *Eigene & Community*.

**KI-Assistent als Notfallkontakt:** Mit *Bei Alarm Bilder ansehen und einschätzen* (aus, fragt vor dem Einschalten)
schaut sich der Assistent höchstens alle 10 Minuten das Alarmbild an, zusammen mit dem, was die Gesichtserkennung auf dem
Pi über vertraute Personen weiß, und schickt den Admins eine kurze Einschätzung; bei einer Eskalation steht sie auch in
der Nachricht an die Notfallkontakte. Die Vergleichsbilder vertrauter Personen verlassen den Pi nie. Die Beispielfragen
im Chat passen zu dem, was eingerichtet ist; Dokumente erreichen das Modell als gekennzeichnete Daten, nie als
Anweisungen.

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
kosten keine Tokens, ebenso Fragen zu pi-guard selbst („Wie funktioniert das VPN?“, „Tutorial“), die aus dem
eingebauten Tutorial kommen; gleiche Fragen kommen eine Stunde lang aus dem Zwischenspeicher, und
ein Tageslimit begrenzt die Kosten. Fragen gehen an den gewählten Anbieter. Der Assistent
bekommt keine Bilder, Namen oder Daten aus Ihrem Zuhause und kann nichts schalten.
Einstellungen erklärt und ändert er unter *Einstellungen → Allgemein → Frag zu Einstellungen*, immer mit Bestätigung und
Rückgängig, siehe [Dashboard](dashboard.md#fragen-zu-einstellungen-und-änderungen-rückgängig-machen).

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

**Kid Protect:** Filter für Kindergeräte und Zeiten ohne Internet, unter *Add-ons → Kid Protect*. Filter brauchen
Pi-hole; Zeiten ohne Internet gehen auch nur mit einer FRITZ!Box.

- **Profile:** eins pro Kind oder eins für alle Kinder. Ein Profil sperrt Arten von Seiten: Erwachseneninhalte (18+),
  Glücksspiel, Schadsoftware und Phishing, Betrug, Drogen, Raubkopien und Torrents, soziale Netzwerke, TikTok. Die
  ersten fünf sind bei einem neuen Profil angekreuzt. Die Listen kommen von
  [The Block List Project](https://blocklistproject.github.io/Lists/) und StevenBlacks Social-Liste; Pi-hole lädt sie.
- **Zeiten ohne Internet:** etwa 21:00 bis 6:30 in Schulnächten, eine Wochenendnacht oder Hausaufgabenzeit, mit Vorlagen
  dafür. Eine Zeit über Mitternacht gehört zum Tag, an dem sie beginnt. *Internet pausieren* schaltet es sofort für 15
  Minuten bis zwei Stunden ab, *Extra-Zeit* gibt Internet während einer Zeit ohne.
- **Geräte:** In der Liste des Heimnetzes für jedes Kindergerät ein Profil wählen; Geräte ohne Profil bleiben unverändert.
  Handys mit privater (wechselnder) WLAN-Adresse brauchen das fürs Heimnetz ausgeschaltet, sonst erkennt das Profil sie
  nicht.

So funktioniert es: Jedes Profil ist eine Pi-hole-Gruppe (`pi-guard-kids-N`), seine Geräte sind Pi-hole-Clients per
MAC-Adresse in dieser Gruppe und der Standardgruppe, und die Listen des Profils gelten nur für die Gruppe, also für
niemanden sonst. Eine Liste, die schon für alle da war, bleibt das. Eine Zeit ohne Internet setzt eine Regel, die für die
gerade gesperrten Gruppen jeden Namen blockiert; mit FRITZ!Box sperrt auch der Router das Internet dieser Geräte, und
danach wird nur zurückgegeben, was Kid Protect gesperrt hat. Die Listen von Kid Protect stehen nicht unter
*Netzwerk → Listen*. Jede Änderung geht sofort an Pi-hole, und das Add-on prüft jede Minute.

DNS-Filter sind kein Schloss: Mobile Daten, ein VPN oder ein anderer DNS-Server umgehen sie. Am meisten helfen sie
jüngeren Kindern, zusammen mit Gesprächen. Getestet mit einem Nachbau der Pi-hole-API, noch nicht mit echtem Pi-hole und
Router.

**Reverse-Proxy:** Dienste hinter NAT über einen Server deiner Wahl erreichen, wie mit frp: dieser Pi als Server oder
Client von `pgproxy`, mit Tokens, erlaubten Ports und festem Zertifikat. Siehe [proxy.md](proxy.md).

**Notfalldaten:** Medizinische Angaben der Menschen im Haus für den Notfall: Blutgruppe, Allergien, Erkrankungen,
Medikamente und Medikamentenplan, Freiverkäufliches und Nahrungsergänzung, Implantate wie ein Herzschrittmacher,
Organspende, anzurufende Personen, Hausarzt und Krankenkasse, dazu Telefon und E-Mail der Person. Bis auf den Namen ist
alles freiwillig.

- **Medikamente** sind Zeilen wie auf dem bundeseinheitlichen Medikationsplan (BMP): Name, Stärke, Wirkstoff, Form,
  Dosis morgens-mittags-abends-nachts oder freies Schema, Einheit, Grund und Hinweis, Dauer- oder Bedarfsmedikation,
  und ob ärztlich verordnet oder in Eigenregie. Was ohne Rezept gekauft wird (Vitamine, Mineralstoffe, pflanzliche
  Mittel), hat eine eigene Sektion.
- **Medikationsplan (BMP) einlesen:** Den Data-Matrix-Code eines gedruckten Plans mit einer Scanner-App lesen und den
  Text einfügen oder die Datei wählen. Die Medikamente landen im Formular; gespeichert wird erst mit *Speichern*.
  *BMP* gibt die Medikamente wieder als solchen Text aus. Den Data-Matrix-Code selbst zeichnet pi-guard nicht. Das folgt
  dem veröffentlichten Aufbau des Plans; Pläne aus einer echten Praxis oder Apotheke sind noch nicht ausprobiert.
- **Organspende** ist eine Auswahl (ja, nein, nur bestimmte Organe, alle außer bestimmten, eine Person entscheidet,
  nicht entschieden) mit Notiz.
- **Markdown** geht in den Textfeldern: `**fett**`, `*kursiv*`, Listen mit `-`, Überschriften mit `#`. *Vorschau* zeigt
  es, das PDF auch. Der **Medikamentenplan** nimmt außerdem eine Markdown-Tabelle (`| Medikament | Morgens | … |`,
  *Tabelle einfügen* legt eine an); gespeichert wie geschrieben, angezeigt als Tabelle, im PDF Zeile für Zeile.
- **Benutzerkonto:** Eine Person lässt sich mit einem Konto verknüpfen. Ein SOS vom Handy dieses Kontos schickt den
  Notfallkontakten dann genau diese Daten, wenn sie dafür eingestellt sind. Wird das Konto gelöscht, endet die
  Verknüpfung.
- **PDF, Bild, Senden:** Ein geöffnetes Blatt lässt sich als PDF (A4, zum Drucken), als Bild (PNG, im Browser gezeichnet,
  zum Teilen vom Handy) speichern und an einen Notfallkontakt senden. Dafür fragt es einmal pro geöffnetem Blatt nach dem
  Passwort; beim Schließen wird es vergessen.

- Verschlüsselt (AES-256-GCM) in einer eigenen Datenbank mit eigenem Schlüssel, nur für den Dienst lesbar; Gelöschtes
  wird überschrieben.
- Angemeldete Admins (und Mitglieder mit diesem Recht) öffnen und ändern sie ohne weiteres Passwort; Dateien, Senden und
  Löschen fragen danach. Zuschauer sehen sie nie.
- Jeder Blick, jede Änderung, jede Datei und jedes Senden steht im Protokoll, mit der Nummer des Eintrags, nie mit dem
  Inhalt.
- Nichts davon geht in Push-Nachrichten oder MQTT. In einen Chat kommt es nur als PDF, wenn ein Admin es schickt oder ein
  SOS es tut, an einen Kontakt, für den das PDF gewählt ist (der Chatdienst behält die Datei dann). Alle anderen Kontakte
  erfahren nur, dass es Notfalldaten gibt und wo sie im Heimnetz oder VPN zu finden sind. Es ist Teil des Datenexports.

Rettungslinks gibt es nicht mehr (bis 1.9): Sie ließen sich nur im Heimnetz oder über das VPN öffnen, wo das Dashboard
ohnehin da ist. Kontakte, die einen Link bekamen, bekommen jetzt den Hinweis.

Frag die Menschen, die du einträgst: Es sind ihre Daten.
