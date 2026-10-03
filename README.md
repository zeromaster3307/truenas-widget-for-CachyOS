# TrueNAS-Status-Widget für KDE Plasma 6 (CachyOS)

Ein kleines Widget für den Plasma-Desktop, das auf einen Blick zeigt, ob auf
Ihrem TrueNAS alles in Ordnung ist – oder ob es **App-Updates**, ein
**Systemupdate** oder **Warnungen** gibt.

| Farbe | Bedeutung |
|---|---|
| 🟢 grün | alles in Ordnung |
| 🟡 gelb | Updates verfügbar (Apps oder System) |
| 🟠 orange | Warnung |
| 🔴 rot | kritische Meldung |
| ⚪ grau | offline / nicht erreichbar / Daten veraltet |

**Annahmen** (bitte prüfen):

- **TrueNAS Community Edition 25.10.x (Goldeye)**, Weboberfläche per HTTPS
  erreichbar (hier im Beispiel Port 5443, selbstsigniertes Zertifikat).
- **KDE Plasma 6**. Prüfen mit `plasmashell --version` – die Ausgabe muss mit
  `plasmashell 6.` beginnen. Für Plasma 5 funktioniert das Widget **nicht**.
- **Python 3.11 oder neuer** (CachyOS hat das vorinstalliert; prüfen mit
  `python3 --version`).

---

## Inhalt

1. [Was das Projekt kann – und was es bewusst nicht tut](#1-was-das-projekt-kann--und-was-es-bewusst-nicht-tut)
2. [API-Key in TrueNAS 25.10 erzeugen](#2-api-key-in-truenas-2510-goldeye-erzeugen)
3. [API-Key sicher ablegen](#3-api-key-sicher-ablegen)
4. [HTTPS und Zertifikats-Fingerabdruck](#4-https-und-zertifikats-fingerabdruck)
5. [Installation, Konfiguration, Benachrichtigungen, Deinstallation](#5-installation-konfiguration-benachrichtigungen-deinstallation)
6. [Fehlersuche](#6-fehlersuche)
7. [Tests (für Entwickler)](#7-tests-für-entwickler)
8. [Technische Details, Quellen und ungeprüfte Punkte](#8-technische-details-quellen-und-ungeprüfte-punkte)

---

## 1. Was das Projekt kann – und was es bewusst NICHT tut

### Was es kann

Das Projekt besteht aus drei Teilen:

1. **Prüfer** (Python). Läuft automatisch alle 15 Minuten (einstellbar) über
   einen systemd-Timer Ihres Benutzers. Er fragt bei TrueNAS ab:
   - **Warnungen (Alerts):** Stufe, Text, ob quittiert. Gezählt werden nur
     **nicht quittierte** Meldungen ab Stufe **WARNING**. Reine Info-Meldungen
     (INFO, NOTICE) werden ignoriert.
   - **Apps:** welche installierten Apps ein Update haben (Name, aktuelle
     Version → neue Version).
   - **Systemupdate:** ja/nein und welche Version.

   Das Ergebnis landet in der kleinen Datei `~/.cache/truenas-widget/status.json`.
   Bei **neuen** Warnungen oder **neuen** Updates gibt es eine
   Desktop-Benachrichtigung – jedes Ereignis nur **einmal**.
2. **Plasma-Widget.** Liest nur diese Datei. Es spricht **nie** selbst mit
   TrueNAS und kennt den API-Key nicht.
   - Im Panel: nur ein farbiger Kreis, beim Darüberfahren eine Kurzinfo.
   - Auf dem Desktop bzw. aufgeklappt: Name + Status. Ist alles in Ordnung,
     steht dort nur der Name und ein grünes „OK“. Sonst darunter nur das
     Relevante (App-Updates, Systemupdate, bis zu 5 Warnungen, dann
     „+ n weitere“). Unten klein die Uhrzeit der letzten Prüfung.
   - Ein Klick öffnet die TrueNAS-Oberfläche im Browser.
   - Ist die Datei älter als 45 Minuten, zeigt das Widget grau „Veraltet“.
3. **Diagnose-Skript.** Prüft die Verbindung, zeigt den
   Zertifikats-Fingerabdruck und listet die **Feldnamen und Datentypen** der
   TrueNAS-Antworten auf (keine Werte). Damit können die Felder gegen Ihr
   echtes System abgeglichen werden.

### Was es bewusst NICHT tut

- **Es ändert nichts auf dem TrueNAS.** Keine Updates installieren, keine
  Neustarts, keine Apps starten oder stoppen, keine Meldungen quittieren.
  Das ist dreifach abgesichert:
  1. Im Programm gibt es eine **feste Liste erlaubter Methoden** (Whitelist):
     `auth.login_ex` (Anmeldung), `alert.list`, `app.query`, `update.status`.
     Jeder andere Aufruf wird verweigert, **bevor** etwas gesendet wird.
  2. Der TrueNAS-Benutzer hat nur eine **schreibgeschützte Rolle**.
  3. Die Tests prüfen, dass Schreibmethoden verweigert werden.
- **Kein unverschlüsseltes HTTP.** `http://` und `ws://` werden abgelehnt.
- **Keine abgeschaltete Zertifikatsprüfung.** Stattdessen wird der
  Fingerabdruck des Zertifikats verglichen (Abschnitt 4).
- **Kein Alarm, wenn das TrueNAS nicht erreichbar ist** (anderes Netz,
  ausgeschaltet …). Dann ist das Widget einfach grau.
- **Der API-Key** steht nie im Repo, nie in Logs, nie in Fehlermeldungen,
  nie in `status.json`.
- Es benutzt **nicht** die alte REST-API (die entfällt mit TrueNAS 26.04),
  sondern die aktuelle JSON-RPC-2.0-Schnittstelle über WebSocket.

---

## 2. API-Key in TrueNAS 25.10 (Goldeye) erzeugen

> Falls Sie Benutzer und Key schon haben: Abschnitt überspringen, aber kurz
> prüfen, dass der Benutzer die Rolle **Readonly Admin** hat.

Die Bezeichnungen unten stammen aus dem Quellcode der TrueNAS-Weboberfläche
Version 25.10.7. Bei deutscher Oberfläche können sie übersetzt sein.

### a) Benutzer anlegen

1. In der TrueNAS-Weboberfläche: **Credentials → Users → Add**.
2. **Username:** z. B. `widget-leser` (frei wählbar, merken).
3. **Password:** lang und zufällig, z. B. mit
   `openssl rand -base64 32` auf Ihrem PC erzeugen. Das Passwort wird nie
   benutzt – Sie müssen es sich nicht merken.
4. Im Bereich **Allow Access**:
   - **SMB Access: Haken ENTFERNEN** (ist standardmässig an!).
   - **Shell Access:** aus.
   - **SSH Access:** aus.
   - Die Option **TrueNAS Access** erst einmal so lassen – siehe b).
5. Speichern.

### b) „TrueNAS Access“ mit schreibgeschützter Rolle

1. Den Benutzer bearbeiten (bzw. direkt beim Anlegen).
2. **TrueNAS Access** aktivieren.
3. Im erscheinenden Auswahlfeld (**Select Role**) die Rolle
   **Readonly Admin** wählen. *Nicht* „Full Admin“ oder „Sharing Admin“.
4. Speichern.

Hinweis: Im Benutzerformular gibt es **keine** Option für API-Keys. Die
Oberfläche schreibt selbst: „Access to API can be granted after user has
been created.“

### c) API-Key auf der eigenen API-Keys-Seite erzeugen

1. Oben rechts auf das **Benutzer-Menü** (Personen-Symbol) klicken →
   **My API Keys**. (Adresse der Seite: `https://<TrueNAS>:<Port>/credentials/users/api-keys`)
2. **Add** klicken.
3. **Name:** z. B. `plasma-widget`.
4. **Username:** im Auswahlfeld den Benutzer aus a) wählen (z. B. `widget-leser`).
5. **Non-expiring** oder ein Ablaufdatum (**Expires On**) – Ihre Wahl.
   Läuft der Key ab, zeigt das Widget „Anmeldung fehlgeschlagen“.
6. Speichern.

### d) Key wird nur EINMAL angezeigt

Den angezeigten Key **sofort** wie in Abschnitt 3 ablegen. Er lässt sich
später nicht mehr anzeigen; bei Verlust einfach einen neuen erzeugen und den
alten löschen.

> ⚠️ **Den Key nie** in einen Chat, eine E-Mail, ein Git-Repo, ein Ticket
> oder einen Screenshot geben. Auch nicht „nur kurz zum Testen“.

---

## 3. API-Key sicher ablegen

Es gibt zwei Wege. **Standard ist die Datei (Weg A).** Weg B (KWallet) ist
etwas sicherer, weil der Key dann verschlüsselt im Schlüsselbund liegt.

### Weg A: Datei mit Rechten 600 (Standard)

Die Befehle funktionieren in bash und fish (Standard-Shell bei CachyOS).

```sh
# 1. Prüfen, ob der Ordner schon existiert (Fehlermeldung = gibt es noch nicht)
ls -ld ~/.config/truenas-widget

# 2. Ordner anlegen und nur für Sie lesbar machen
mkdir -p ~/.config/truenas-widget
chmod 700 ~/.config/truenas-widget

# 3. Leere Key-Datei anlegen und Rechte setzen, BEVOR der Key hineinkommt
touch ~/.config/truenas-widget/api-key
chmod 600 ~/.config/truenas-widget/api-key

# 4. Key einfügen: Editor öffnen, Key mit Strg+Umschalt+V einfügen,
#    Strg+O, Enter (speichern), Strg+X (beenden)
nano ~/.config/truenas-widget/api-key

# 5. Rechte kontrollieren
ls -l ~/.config/truenas-widget/api-key
```

Die letzte Ausgabe muss mit **`-rw-------`** beginnen. Sind die Rechte
offener, gibt das Programm eine **Warnung** aus und nennt den Befehl zum
Reparieren.

In die Datei gehört **nur der Key**, in einer Zeile, ohne Anführungszeichen.

### Weg B: Secret-Service mit `secret-tool` (KWallet/libsecret)

1. Prüfen, ob `secret-tool` vorhanden ist: `secret-tool --help`.
   Falls nicht: `sudo pacman -S libsecret`.
2. Key speichern (Sie werden nach dem „Password“ gefragt – dort den Key
   einfügen; die Eingabe ist unsichtbar):

   ```sh
   secret-tool store --label="TrueNAS-Widget API-Key" service truenas-widget account api-key
   ```
3. Prüfen, **ob** er gefunden wird, ohne ihn anzuzeigen:

   ```sh
   secret-tool lookup service truenas-widget account api-key > /dev/null && echo gefunden
   ```
4. In `~/.config/truenas-widget/config.toml` umstellen:

   ```toml
   [key]
   source = "secret-tool"
   ```
5. Eine eventuell vorhandene Key-Datei löschen: `rm ~/.config/truenas-widget/api-key`

**Ungeprüft:** Ob KWallet auf Ihrem System den Secret-Service bereitstellt,
hängt von den Einstellungen ab (Systemeinstellungen → KDE-Passwortspeicher).
Ist der Schlüsselbund nach dem Anmelden noch gesperrt, kann der Prüfer den
Key nicht lesen und das Widget wird grau. Dann Weg A benutzen.

---

## 4. HTTPS und Zertifikats-Fingerabdruck

### Warum kein HTTP?

TrueNAS **widerruft einen API-Key sofort**, wenn er über eine unverschlüsselte
Verbindung benutzt wird (im TrueNAS-Quellcode belegt: „Attempt to use over an
insecure transport“). Deshalb lehnt das Programm `http://` und `ws://` mit
einer klaren Fehlermeldung ab. Tragen Sie in der Konfiguration nur die
Adresse ein (z. B. `192.168.1.20`), ohne Präfix.

### Warum ein Fingerabdruck?

Ihr TrueNAS benutzt ein selbstsigniertes Zertifikat. Die übliche Prüfung
(„ist das Zertifikat von einer bekannten Stelle unterschrieben?“) schlägt
deshalb immer fehl. Viele Programme schalten die Prüfung dann einfach ab –
das wäre unsicher, denn dann könnte sich jedes Gerät im Netz als TrueNAS
ausgeben und den Key abgreifen.

Dieses Programm macht es anders: Es kennt den **SHA-256-Fingerabdruck**
(eine Art eindeutige Prüfsumme) Ihres TrueNAS-Zertifikats und baut die
Verbindung **nur** auf, wenn er exakt übereinstimmt. Stimmt er nicht, wird
die Verbindung getrennt, **bevor** irgendetwas – erst recht nicht der Key –
gesendet wird.

### Fingerabdruck ermitteln und eintragen

1. Adresse und Port in `~/.config/truenas-widget/config.toml` eintragen
   (Abschnitt 5).
2. Fingerabdruck anzeigen lassen (es wird dabei nichts gesendet):

   ```sh
   ./diagnose.sh --nur-fingerabdruck
   ```
   Ausgabe z. B. `3F:A2:…:9C` (32 Paare).
3. **Bewusst vergleichen**, bevor Sie ihn übernehmen. Möglichkeiten:
   - Im Browser die TrueNAS-Seite öffnen → auf das Schloss-/Warnsymbol links
     neben der Adresse klicken → Zertifikat anzeigen → **SHA-256-Fingerabdruck**.
   - Oder mit openssl:
     ```sh
     openssl s_client -connect 192.168.1.20:5443 </dev/null 2>/dev/null | openssl x509 -noout -fingerprint -sha256
     ```
   Beide Wege laufen über dasselbe Netz. Am sichersten ist der Vergleich zu
   Hause im eigenen LAN, wenn Sie sicher sind, dass niemand dazwischen sitzt.
4. Wert in die Konfiguration eintragen (mit oder ohne Doppelpunkte, Gross-/
   Kleinschreibung egal):

   ```toml
   fingerprint_sha256 = "3F:A2:…:9C"
   ```

### Wenn sich der Fingerabdruck ändert

Erneuern Sie das Zertifikat auf dem TrueNAS (oder läuft es ab und wird neu
erzeugt), ändert sich der Fingerabdruck. Das Widget wird dann grau mit dem
Hinweis „Zertifikats-Fingerabdruck stimmt nicht überein“. Das ist **Absicht**:
Es kann ein harmloser Zertifikatswechsel sein – oder ein Angriff. Prüfen Sie
den neuen Wert wie oben und tragen Sie ihn erst dann ein.

---

## 5. Installation, Konfiguration, Benachrichtigungen, Deinstallation

### Installation

```sh
# 1. Repo holen (Adresse Ihres privaten Repos)
git clone https://github.com/<IHR-NAME>/truenas-widget-for-CachyOS.git
cd truenas-widget-for-CachyOS

# 2. Installieren (als normaler Benutzer, NICHT mit sudo)
./install.sh
```

`install.sh` macht Folgendes (erneut ausführen ist gefahrlos):

- kopiert den Prüfer nach `~/.local/share/truenas-widget/`,
- legt `~/.config/truenas-widget/` an (Rechte 700) und kopiert die
  Beispiel-Konfiguration als `config.toml` dorthin – **nur falls noch keine
  existiert**,
- richtet den systemd-User-Timer ein und startet ihn,
- installiert das Plasma-Widget (`kpackagetool6`).

Danach:

1. Konfiguration anpassen (siehe unten).
2. Fingerabdruck eintragen (Abschnitt 4).
3. API-Key ablegen (Abschnitt 3).
4. Diagnose ausführen: `./diagnose.sh`
5. Einmal sofort prüfen lassen:
   `systemctl --user start truenas-widget.service`
6. Widget hinzufügen: Rechtsklick auf Panel oder Desktop → **Widgets
   hinzufügen…** → nach **TrueNAS-Status** suchen → hineinziehen.

### Konfiguration

Datei: `~/.config/truenas-widget/config.toml` (Vorlage:
[`config.example.toml`](config.example.toml)). Öffnen z. B. mit
`kate ~/.config/truenas-widget/config.toml`.

| Einstellung | Bedeutung |
|---|---|
| `name` | Anzeigename im Widget |
| `host` | Adresse des TrueNAS, **ohne** `http://` |
| `port` | HTTPS-Port der Weboberfläche (z. B. 5443) |
| `username` | Benutzer, dem der API-Key gehört |
| `fingerprint_sha256` | Zertifikats-Fingerabdruck (Abschnitt 4) |
| `api_version` | API-Version im Pfad, Standard `v25.10.0` |
| `[key] source` | `"file"` (Standard) oder `"secret-tool"` |
| `[checker] interval_minutes` | Prüfabstand in Minuten (Standard 15) |
| `[checker] timeout_seconds` | Wartezeit auf TrueNAS |
| `[notifications] enabled` | Benachrichtigungen an/aus |

Nach Änderung von `interval_minutes` bitte `./install.sh` erneut ausführen
(stellt den Timer um). Andere Änderungen wirken bei der nächsten Prüfung.

### Benachrichtigungen abschalten

In `config.toml`:

```toml
[notifications]
enabled = false
```

Der Prüfer merkt sich trotzdem, was schon bekannt ist. Beim Wieder-Einschalten
werden also keine alten Meldungen „nachgeholt“.

Wann gibt es eine Benachrichtigung?

- bei einem **neuen** nicht quittierten Alert ab WARNING,
- bei einem **neuen** App-Update (App + neue Version),
- bei einem **neuen** Systemupdate (Version).

Bereits gemeldete Ereignisse stehen in `~/.local/state/truenas-widget/notified.json`.
Ist TrueNAS nicht erreichbar, gibt es **keine** Benachrichtigung.

### Nützliche Befehle

```sh
systemctl --user status truenas-widget.timer          # läuft der Timer? nächste Prüfung?
systemctl --user start truenas-widget.service         # jetzt sofort prüfen
journalctl --user -u truenas-widget.service -n 30     # letzte Meldungen des Prüfers
cat ~/.cache/truenas-widget/status.json               # aktuelles Ergebnis
```

### Deinstallation

```sh
./uninstall.sh
```

Entfernt Timer, Prüfer, Widget, `status.json` und Zustandsdatei. **Nicht**
gelöscht werden Ihre Konfiguration und der Key in `~/.config/truenas-widget/`
– das Skript zeigt an, wie Sie diese selbst löschen. Vergessen Sie nicht,
den API-Key danach in TrueNAS (**My API Keys**) zu löschen.

---

## 6. Fehlersuche

Zuerst immer: `./diagnose.sh` ausführen und die Ausgabe lesen. Sie enthält
keinen Key, keine Adresse und keine Werte und darf weitergegeben werden.

| Anzeige / Meldung | Ursache | Lösung |
|---|---|---|
| Grau, „Nicht erreichbar“ | Anderes Netz, TrueNAS aus, falsche Adresse/Port | Im selben LAN? `host`/`port` prüfen. Im Browser `https://<host>:<port>` öffnen. |
| Grau, „Zertifikats-Fingerabdruck stimmt nicht überein“ | Zertifikat erneuert – oder ein fremdes Gerät | Abschnitt 4: neuen Wert **prüfen**, dann eintragen |
| Grau, „Anmeldung fehlgeschlagen (AUTH_ERR)“ | Benutzername oder Key falsch, Key gelöscht | `username` prüfen; Key neu erzeugen und ablegen |
| Grau, „Anmeldung fehlgeschlagen (EXPIRED)“ | Key abgelaufen **oder widerrufen** (z. B. weil er einmal über HTTP benutzt wurde) | Neuen Key erzeugen |
| „Zugriff verweigert für …“ | Benutzer hat nicht die Rolle **Readonly Admin** | Abschnitt 2b. Diagnose zeigt, welche Abfrage betroffen ist |
| Grau, „Daten unvollständig“ | Wie oben: eine Abfrage wurde verweigert, daher kann „OK“ nicht bestätigt werden | Rolle prüfen |
| Warnung „Key-Datei hat zu offene Rechte“ | Andere Benutzer könnten den Key lesen | `chmod 600 ~/.config/truenas-widget/api-key` und `chmod 700 ~/.config/truenas-widget` |
| „Key-Datei nicht gefunden“ / „leer“ | Abschnitt 3 nicht erledigt | Abschnitt 3 |
| „Kein Zertifikats-Fingerabdruck eingetragen“ | Platzhalter noch in der Konfiguration | Abschnitt 4 |
| Grau, „Veraltet“ | Prüfer läuft nicht (oder PC war im Ruhezustand) | `systemctl --user status truenas-widget.timer`, `journalctl --user -u truenas-widget.service -n 30` |
| Grau, „Noch keine Daten“ | Prüfer lief noch nie | `systemctl --user start truenas-widget.service` |
| Keine Benachrichtigungen | abgeschaltet, `notify-send` fehlt, oder Ereignis schon gemeldet | `[notifications] enabled`, `sudo pacman -S libnotify` |
| Widget nicht in der Liste | Kein Plasma 6 oder Installation fehlgeschlagen | `plasmashell --version`, `./install.sh` erneut |
| „WebSocket-Aufbau abgelehnt (HTTP 404)“ | API-Pfad passt nicht zur TrueNAS-Version | `api_version = "current"` probieren und Diagnose erneut ausführen |

---

## 7. Tests (für Entwickler)

```sh
python3 -m unittest -v
```

Die Tests brauchen kein TrueNAS: Sie benutzen Mock-Antworten und einen
kleinen Test-Server (echtes TLS + WebSocket auf `127.0.0.1` mit einem bei
jedem Lauf neu erzeugten Test-Zertifikat; benötigt `openssl`). Die
Widget-Logik wird mit Node.js geprüft (wird übersprungen, falls nicht
installiert). Abgedeckt sind u. a.:

alles ok · App-Updates · Systemupdate · Warnung · kritisch · quittierter Alert
(ignoriert) · Info-Alert (ignoriert) · nicht erreichbar · falscher
Fingerabdruck (es wird nichts gesendet) · fehlende Rechte · Whitelist
verweigert Schreibmethoden · `http://` wird abgelehnt · Key-Datei mit zu
offenen Rechten · Benachrichtigung nur einmal pro Ereignis · veraltete
status.json · max. 5 Alert-Zeilen. Jeder Test prüft, dass der Key in keinem
Log auftaucht.

Vor jedem Commit: `./tools/check-secrets.sh` (sucht nach IP-Adressen,
E-Mail-Adressen, Fingerabdrücken und Schlüsseln in den Repo-Dateien).

---

## 8. Technische Details, Quellen und ungeprüfte Punkte

### Aufbau des Repos

```
truenas_widget/        Prüfer (Python, nur Standardbibliothek)
  config.py            Konfiguration lesen, http:// ablehnen
  keystore.py          Key aus Datei/secret-tool, Rechte-Warnung
  wsclient.py          WebSocket über TLS mit Fingerabdruck-Prüfung
  rpc.py               JSON-RPC mit Whitelist
  checker.py           Abfragen, Gesamtstatus, status.json
  notify.py            Benachrichtigungen, jedes Ereignis einmal
  diagnose.py          Diagnose
plasmoid/package/      Plasma-6-Widget (metadata.json, QML, logic.js)
systemd/               Service + Timer (Vorlagen für install.sh)
tests/                 Unit- und Ende-zu-Ende-Tests mit Attrappe
install.sh, uninstall.sh, diagnose.sh
config.example.toml    Beispiel-Konfiguration (nur Platzhalter)
```

### Warum keine WebSocket-Bibliothek?

Der Prüfer benutzt **nur die Python-Standardbibliothek** und einen kleinen
eigenen WebSocket-Client (ca. 300 Zeilen inkl. Kommentaren). Gründe: Auf CachyOS/Arch soll man
nicht mit `pip` ins System-Python installieren; ein Zusatzpaket müsste extra
gepflegt werden; und vor allem baut der eigene Client die TLS-Verbindung
selbst auf und kann so den Fingerabdruck prüfen, **bevor** ein einziges Byte
gesendet wird.

### Benutzte TrueNAS-Schnittstelle (belegt)

Die Doku-Seite <https://api.truenas.com/v25.10> war aus der
Entwicklungsumgebung **nicht erreichbar** (Netzwerksperre). Alles unten ist
deshalb direkt im **TrueNAS-Quellcode Version 25.10.7** nachgeprüft
(github.com/truenas/middleware und github.com/truenas/webui, jeweils Tag
`TS-25.10.7`). Die Doku wird aus diesem Quellcode erzeugt.

| Was | Wert | Quelle im Quellcode |
|---|---|---|
| Endpunkt | `wss://<host>:<port>/api/v25.10.0` (auch `/api/current`) | `middlewared/main.py`: Route `/api/{version}` |
| Anmeldung | `auth.login_ex` mit `{"mechanism": "API_KEY_PLAIN", "username", "api_key"}`, Antwort `response_type` = `SUCCESS` | `api/v25_10_0/auth.py` |
| Key-Widerruf bei HTTP | ja, „Attempt to use over an insecure transport“ | `plugins/auth.py` |
| Alerts | `alert.list` → Felder `uuid`, `level`, `dismissed`, `text`, `formatted`, … | `api/v25_10_0/alert.py`, Rolle `ALERT_LIST_READ` |
| Alert-Stufen | INFO, NOTICE, WARNING, ERROR, CRITICAL, ALERT, EMERGENCY | `api/v25_10_0/alert.py` (`AlertLevel`) |
| Apps | `app.query` → `name`, `version`, `human_version`, `latest_version`, `upgrade_available`, `image_updates_available`, `custom_app` | `api/v25_10_0/app.py`, Rolle `APPS_READ` |
| Systemupdate | `update.status` → `code`, `status.new_version.version`, `error` | `api/v25_10_0/update.py`, Rolle `SYSTEM_UPDATE_READ` |
| Fehlende Rechte | Fehler mit `errname` = `EACCES`, „Not authorized“ | `middlewared/main.py` |
| Rolle „Readonly Admin“ | enthält alle `*_READ`-Rollen | `middlewared/role.py` |
| Oberfläche | Benutzer-Menü → „My API Keys“; Benutzerformular „TrueNAS Access“ + Rolle; „SMB Access“ standardmässig an | webui `user-menu.component.html`, `allowed-access-section` |

Die Schemas für Alerts, Apps, Updates und Anmeldung sind in allen
API-Versionen von v25.10.0 bis v25.10.5 identisch.

### Abweichungen vom ursprünglichen Auftrag (laut Quellcode)

- **Alert-Stufen:** TrueNAS kennt mehr als „Warnung/kritisch“. Zuordnung hier:
  INFO und NOTICE → ignoriert; WARNING → **Warnung**; ERROR, CRITICAL, ALERT,
  EMERGENCY → **kritisch**. Unbekannte Stufen werden vorsichtshalber wie
  WARNING behandelt.
- **Alert-Text:** Das Feld `text` ist oft nur eine Vorlage mit Platzhaltern
  (z. B. `%(pool)s`). Angezeigt wird deshalb das Feld `formatted` (HTML
  entfernt), sonst `text`.
- **„Update-Prüfung“** heisst in 25.10 `update.status` (es gibt kein
  `update.check_available` mehr).
- **Anmeldung** ist keine Lese-Methode, steht aber notwendigerweise auf der
  Whitelist. Sie ändert nichts am System.
- **App-Updates:** Als Update zählt `upgrade_available` **oder**
  `image_updates_available` (bei eigenen Apps gibt es oft nur ein neues
  Container-Image; dann steht „neues Image“ statt einer Versionsnummer).

### UNGEPRÜFT – bitte mit der Diagnose am echten System bestätigen

Diese Punkte konnten ohne Ihr System nicht getestet werden:

1. **Alles gegen das echte TrueNAS** – getestet wurde nur mit Attrappen.
2. **`select` bei `app.query`:** Die Option ist offiziell dokumentiert;
   ob TrueNAS damit fehlerfrei nur die gewünschten Felder liefert, zeigt die
   Diagnose.
3. **Anzeige der App-Versionen:** Es wird `version → latest_version`
   (Katalog-Versionen) angezeigt. Ob das der Darstellung in der
   TrueNAS-Oberfläche entspricht, ist ungeprüft.
4. **Port 5443 und Pfad `/api/…`:** Annahme, dass die Weboberfläche auf diesem
   Port auch die API ausliefert (bei TrueNAS üblich).
5. **Plasma-Widget im echten Plasma 6:** Die QML-Datei wurde nur auf
   Syntaxfehler geprüft (qmllint) und die Anzeige-Logik mit Tests. Die
   benutzten Plasma-Bausteine (`PlasmoidItem`, `plasma5support`-Datenquelle
   „executable“) sind im KDE-Quellcode Plasma 6.4 belegt; ein Probelauf auf
   einem echten Desktop fehlt.
6. **KWallet als Secret-Service** (Abschnitt 3, Weg B).
7. **Benachrichtigungen** über `notify-send` aus dem systemd-User-Dienst
   (funktioniert unter Plasma normalerweise; nicht auf Ihrem System getestet).
8. **Bezeichnungen in der deutschen TrueNAS-Oberfläche** – hier stehen die
   englischen Originalnamen aus dem Quellcode.

### Lizenz

Noch keine Lizenz festgelegt. Bitte vor einer Veröffentlichung eine wählen.
