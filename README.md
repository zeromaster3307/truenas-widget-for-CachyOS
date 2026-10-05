# TrueNAS-Status-Widget für KDE Plasma 6 (CachyOS)

Ein kleines Widget für den Plasma-Desktop, das auf einen Blick zeigt, ob auf
Ihrem TrueNAS – oder mehreren – alles in Ordnung ist, oder ob es
**App-Updates**, ein **Systemupdate** oder **Warnungen** gibt.

| Farbe | Bedeutung |
|---|---|
| 🟢 grün | alles in Ordnung |
| 🟡 gelb | Updates verfügbar (Apps oder System) |
| 🟠 orange | Warnung – bei mehreren Systemen auch: ein System ist wiederholt nicht erreichbar, obwohl ein anderes antwortet |
| 🔴 rot | kritische Meldung |
| ⚪ grau | offline / nicht erreichbar / Daten veraltet / noch nicht eingerichtet |

**Version 0.4.1.** Neu gegenüber 0.3: mehrere TrueNAS-Systeme in einem Widget,
Einrichtungs-Assistent, „offline ignorieren“ pro System.

**Annahmen** (bitte prüfen):

- **TrueNAS Community Edition 25.10.x (Goldeye)**, Weboberfläche per HTTPS
  erreichbar (in den Beispielen Port 443, selbstsigniertes Zertifikat).
  Auch ein System, das nur über **Tailscale** erreichbar ist, geht – solange
  die HTTPS-Weboberfläche des TrueNAS direkt angesprochen wird.
- **KDE Plasma 6**. Prüfen mit `plasmashell --version` – die Ausgabe muss mit
  `plasmashell 6.` beginnen. Für Plasma 5 funktioniert das Widget **nicht**.
- **Python 3.11 oder neuer** (CachyOS hat das vorinstalliert; prüfen mit
  `python3 --version`).
- **kdialog** für die Fenster des Einrichtungs-Assistenten (prüfen mit
  `kdialog --version`, sonst `sudo pacman -S kdialog`). Ohne kdialog fragt der
  Assistent im Terminal.

---

## Inhalt

1. [Was das Projekt kann – und was es bewusst nicht tut](#1-was-das-projekt-kann--und-was-es-bewusst-nicht-tut)
2. [Vorbereitung auf dem TrueNAS: Benutzer, Rolle, API-Key](#2-vorbereitung-auf-dem-truenas-benutzer-rolle-api-key)
3. [Installieren und ein TrueNAS einrichten (Assistent)](#3-installieren-und-ein-truenas-einrichten-assistent)
4. [HTTPS und Zertifikats-Fingerabdruck](#4-https-und-zertifikats-fingerabdruck)
5. [Bedienung, mehrere Systeme, Konfiguration, Deinstallation](#5-bedienung-mehrere-systeme-konfiguration-deinstallation)
6. [Fehlersuche](#6-fehlersuche)
7. [Tests (für Entwickler)](#7-tests-für-entwickler)
8. [Technische Details, Quellen und ungeprüfte Punkte](#8-technische-details-quellen-und-ungeprüfte-punkte)

---

## 1. Was das Projekt kann – und was es bewusst NICHT tut

### Was es kann

Das Projekt besteht aus vier Teilen:

1. **Prüfer** (Python). Läuft automatisch alle 15 Minuten (einstellbar) über
   einen systemd-Timer Ihres Benutzers und fragt **alle eingerichteten
   TrueNAS-Systeme gleichzeitig** ab:
   - **Warnungen (Alerts):** Stufe, Text, ob quittiert. Gezählt werden nur
     **nicht quittierte** Meldungen ab Stufe **WARNING**. Reine Info-Meldungen
     (INFO, NOTICE) werden ignoriert.
   - **Apps:** welche installierten Apps ein Update haben (Name, aktuelle
     Version → neue Version).
   - **Systemupdate:** ja/nein und welche Version.

   Das Ergebnis landet in der kleinen Datei `~/.cache/truenas-widget/status.json`.
   Bei **neuen** Warnungen oder **neuen** Updates gibt es eine
   Desktop-Benachrichtigung – jedes Ereignis nur **einmal** (pro System).
2. **Plasma-Widget.** Liest nur diese Datei. Es spricht **nie** selbst mit
   TrueNAS und kennt keinen API-Key.
   - **Im Panel:** nur ein farbiger Kreis (Gesamtstatus aller Systeme), beim
     Darüberfahren eine Kurzinfo pro System. Ein Klick klappt die Details auf.
   - **Auf dem Desktop:** immer direkt die Details, egal wie gross das Widget
     gezogen ist.
   - **Details:** je System ein farbiger Punkt, Name und Status. Ist ein
     System in Ordnung, steht dort nur sein Name und ein grünes „OK“. Sonst
     darunter nur das Relevante (App-Updates, Systemupdate, Warnungen – bei
     einem System bis zu 5, bei mehreren bis zu 3 pro System, dann
     „+ n weitere“). Ganz unten gemeinsam **„Letzte Prüfung: hh:mm“** und der
     Knopf **„Aktualisieren“**.
   - Ein Klick auf ein System öffnet **dessen** TrueNAS-Oberfläche im Browser.
   - Ist die Datei älter als 45 Minuten, zeigt das Widget grau „Veraltet“.
   - **Beim Start** (z. B. nach dem Anmelden) prüft das Widget, ob die Daten
     älter als das Prüfintervall sind, und stösst dann **einmal** eine
     Prüfung an.
   - **Rechtsklick-Menü:** „Jetzt prüfen“, bei mehreren Systemen je System
     „offline ignorieren“, und „TrueNAS hinzufügen/verwalten…“.
3. **Einrichtungs-Assistent.** Fenster, die Schritt für Schritt ein TrueNAS
   hinzufügen (Adresse, Fingerabdruck bestätigen, Key eingeben, Verbindung
   testen) – und Systeme ändern, entfernen, Key erneuern, Fingerabdruck neu
   prüfen. Kein Herumtippen in Konfigurationsdateien nötig.
4. **Diagnose-Skript.** Prüft die Verbindung, zeigt den
   Zertifikats-Fingerabdruck und listet die **Feldnamen und Datentypen** der
   TrueNAS-Antworten auf (keine Werte). Damit können die Felder gegen Ihr
   echtes System abgeglichen werden.

### Was es bewusst NICHT tut

- **Es ändert nichts auf dem TrueNAS.** Keine Updates installieren, keine
  Neustarts, keine Apps starten oder stoppen, keine Meldungen quittieren.
  Das ist dreifach abgesichert:
  1. Im Programm gibt es eine **feste Liste erlaubter Methoden** (Whitelist):
     `auth.login_ex` (Anmeldung), `alert.list`, `app.query`, `update.status`.
     Jeder andere Aufruf wird verweigert, **bevor** etwas gesendet wird. Das
     gilt auch für den Verbindungstest im Assistenten.
  2. Der TrueNAS-Benutzer hat nur eine **schreibgeschützte Rolle**.
  3. Die Tests prüfen, dass Schreibmethoden verweigert werden.
- **Kein unverschlüsseltes HTTP.** `http://` und `ws://` werden abgelehnt.
- **Keine abgeschaltete Zertifikatsprüfung.** Stattdessen wird der
  Fingerabdruck des Zertifikats verglichen (Abschnitt 4).
- **Das Widget spricht nie mit TrueNAS und kennt keinen Key.** Es liest nur
  `status.json` und darf genau **zwei** Befehle starten, beide ohne
  Parameter, ohne Key, ohne Adresse:
  1. `systemctl --user start truenas-widget.service` (die Prüfung),
  2. `~/.local/share/truenas-widget/setup.sh` (den Einrichtungs-Assistenten).
- **Den API-Key gibt man nur im Assistenten ein**, in einem verdeckten Feld.
  Er geht von dort direkt in eine Datei mit Rechten 600 oder in KWallet – nie
  durch das Widget, nie über eine Befehlszeile.
- **Kein Alarm, wenn ein einzelnes TrueNAS nicht erreichbar ist** (anderes
  Netz, ausgeschaltet …). Dann ist das Widget einfach grau. Bei mehreren
  Systemen siehe Abschnitt 5 („offline ignorieren“).
- **Der API-Key** steht nie im Repo, nie in Logs, nie in Fehlermeldungen,
  nie in `status.json`.
- Es benutzt **nicht** die alte REST-API (die entfällt mit TrueNAS 26.04),
  sondern die aktuelle JSON-RPC-2.0-Schnittstelle über WebSocket.

---

## 2. Vorbereitung auf dem TrueNAS: Benutzer, Rolle, API-Key

Das muss **auf jedem TrueNAS** einmal gemacht werden, das Sie überwachen
wollen. Der Assistent (Abschnitt 3) zeigt diese Schritte auch noch einmal an
und öffnet die API-Keys-Seite im Browser.

> Falls Sie Benutzer und Key schon haben: überspringen, aber kurz prüfen,
> dass der Benutzer die Rolle **Readonly Admin** hat.

Die Bezeichnungen stammen aus dem Quellcode der TrueNAS-Weboberfläche
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
5. Speichern.

### b) „TrueNAS Access“ mit schreibgeschützter Rolle

1. Den Benutzer bearbeiten (bzw. direkt beim Anlegen).
2. **TrueNAS Access** aktivieren.
3. Im erscheinenden Auswahlfeld (**Select Role**) die Rolle
   **Readonly Admin** wählen. *Nicht* „Full Admin“ oder „Sharing Admin“.
4. Speichern.

Im Benutzerformular gibt es **keine** Option für API-Keys. Die Oberfläche
schreibt selbst: „Access to API can be granted after user has been created.“

### c) API-Key auf der eigenen API-Keys-Seite erzeugen

1. Oben rechts auf das **Benutzer-Menü** (Personen-Symbol) klicken →
   **My API Keys**. (Adresse: `https://<TrueNAS>:<Port>/credentials/users/api-keys`)
2. **Add** klicken.
3. **Name:** z. B. `plasma-widget`.
4. **Username:** im Auswahlfeld den Benutzer aus a) wählen.
5. **Non-expiring** oder ein Ablaufdatum (**Expires On**) – Ihre Wahl.
   Läuft der Key ab, zeigt das Widget „Anmeldung fehlgeschlagen“.
6. Speichern. **Der Key wird nur EINMAL angezeigt** – gleich im Assistenten
   eingeben (Abschnitt 3). Bei Verlust einfach neu erzeugen und den alten
   löschen.

> ⚠️ **Den Key nie** in einen Chat, eine E-Mail, ein Git-Repo, ein Ticket
> oder einen Screenshot geben. Auch nicht „nur kurz zum Testen“.

---

## 3. Installieren und ein TrueNAS einrichten (Assistent)

### Installieren

```sh
# 1. Repo holen (Adresse Ihres privaten Repos)
mkdir -p ~/Projekte/Programme
cd ~/Projekte/Programme
git clone https://github.com/<IHR-NAME>/truenas-widget-for-CachyOS.git
cd truenas-widget-for-CachyOS

# 2. Installieren (als normaler Benutzer, NICHT mit sudo)
./install.sh
```

`install.sh` macht Folgendes (erneut ausführen ist gefahrlos):

- kopiert Prüfer und Assistent nach `~/.local/share/truenas-widget/` und legt
  den Eintrag **„TrueNAS-Widget einrichten“** im Anwendungsmenü an,
- legt `~/.config/truenas-widget/` an (Rechte 700) mit `config.toml`
  (allgemeine Einstellungen) – **nur falls noch keine existiert**,
- stellt eine `config.toml` aus Version 0.3 automatisch um (siehe unten),
- richtet den systemd-User-Timer ein (`systemctl --user enable --now`) und
  stösst eine erste Prüfung im Hintergrund an,
- installiert bzw. aktualisiert das Plasma-Widget (`kpackagetool6`).

Danach das Widget hinzufügen: Rechtsklick auf Panel oder Desktop →
**Widgets hinzufügen…** → nach **TrueNAS-Status** suchen → hineinziehen.

### Ein TrueNAS einrichten

Den Assistenten starten – drei gleichwertige Wege:

- **Rechtsklick auf das Widget → „TrueNAS hinzufügen/verwalten…“**
  (solange noch nichts eingerichtet ist, auch über den Knopf **„Einrichten…“**)
- Anwendungsmenü → **„TrueNAS-Widget einrichten“**
- Terminal im Repo-Ordner: `./setup.sh` (bzw. `./setup.sh --terminal` ohne Fenster)

Der Assistent fragt nacheinander:

1. **Anzeigename** (z. B. `homelab` oder `remote`).
2. **Adresse** (IP oder Name, z. B. `192.168.1.20` oder
   `nas.tailnet.ts.net`) – `http://` wird abgelehnt.
3. **Port** (meist `443`).
4. **Fingerabdruck:** zeigt den SHA-256-Fingerabdruck des Zertifikats samt
   Anleitung, wo Sie ihn vergleichen können (TrueNAS-Shell, Firefox, Chrome –
   siehe Abschnitt 4), und öffnet auf Wunsch die TrueNAS-Seite im Browser.
   Erst nach Ihrem **„Ja, stimmt überein“** geht es weiter – vorher wird
   nichts gesendet.
5. **Benutzername** auf dem TrueNAS (Vorgabe `widget-leser`).
6. Auf Wunsch öffnet er die Seite **My API Keys** dieses TrueNAS.
7. **Speicherort für den Key** (nur wenn `secret-tool` vorhanden ist):
   Datei (Standard) oder KDE-Passwortspeicher/KWallet.
8. **API-Key** in einem verdeckten Feld.
9. **Verbindungstest** (Anmeldung + die drei Lese-Abfragen, mit bis zu 45 s
   Wartezeit). Der Test benutzt denselben Code wie die regelmässige Prüfung –
   er bringt keine zusätzliche Sicherheit, sondern sofortige Rückmeldung:
   - **Falscher Key oder Benutzer:** Key gleich neu eingeben (gespeichert wird
     so nicht).
   - **Zeitüberschreitung / keine Verbindung:** Der Assistent sagt, ob gar
     keine Verbindung zustande kam oder das TrueNAS nur zu langsam antwortete,
     nennt mögliche Gründe (z. B. Tailscale) und bietet **„Nochmal testen“**
     oder **„Trotzdem speichern“** an.
   - **Fehlende Rechte:** Hinweis auf die Rolle „Readonly Admin“, dann
     „Nochmal testen“ oder „Trotzdem speichern“.
10. Speichert alles und stösst eine Prüfung an – das System erscheint nach
    wenigen Sekunden im Widget.

Ist schon ein System eingerichtet, zeigt der Assistent ein Menü:
**Neues TrueNAS hinzufügen**, und pro System **Name/Adresse/Benutzer ändern**,
**API-Key erneuern**, **Zertifikats-Fingerabdruck neu prüfen**, **entfernen**.

### Wo was gespeichert wird

| Datei | Inhalt |
|---|---|
| `~/.config/truenas-widget/config.toml` | allgemeine Einstellungen (Intervall, Benachrichtigungen) |
| `~/.config/truenas-widget/systems/<kennung>.toml` | ein TrueNAS: Name, Adresse, Port, Benutzer, Fingerabdruck – **kein Key** (Rechte 600) |
| `~/.config/truenas-widget/keys/<kennung>` | der API-Key dieses Systems (Rechte 600, Ordner 700) – oder stattdessen KWallet |

`<kennung>` bildet der Assistent aus dem Anzeigenamen (z. B. „Mein NAS“ →
`mein-nas`). Sie bleibt auch beim Umbenennen gleich.

### Update von Version 0.3

Einfach im Repo-Ordner:

```sh
git pull
./install.sh
systemctl --user restart plasma-plasmashell.service
```

`install.sh` erkennt die alte `config.toml` (Abschnitt `[truenas]`) und
stellt sie einmalig um:

- Ihr System wird zu `systems/<name>.toml` (z. B. `systems/homelab.toml`).
- **Die Key-Datei bleibt, wo sie ist** (`~/.config/truenas-widget/api-key`);
  die neue Systemdatei verweist darauf. Der Key wird dabei nicht gelesen.
- Die alte Datei bleibt als `config.toml.v0.3.bak` liegen.
- Bereits gemeldete Ereignisse werden übernommen – es kommen also keine
  alten Benachrichtigungen erneut.

### Zurück zu Version 0.3 (falls nötig)

Version 0.4 ist **nicht** mit 0.3 kompatibel. Zurück geht es so:

```sh
cd ~/Projekte/Programme/truenas-widget-for-CachyOS
git checkout 18f0e70                     # letzter Stand von 0.3 (Widget 0.3.1)
cp ~/.config/truenas-widget/config.toml.v0.3.bak ~/.config/truenas-widget/config.toml
./install.sh
systemctl --user restart plasma-plasmashell.service
```

Wieder zurück auf den aktuellen Stand: `git checkout claude/amazing-gauss-ywlz0n`
und erneut `./install.sh`.

---

## 4. HTTPS und Zertifikats-Fingerabdruck

### Warum kein HTTP?

TrueNAS **widerruft einen API-Key sofort**, wenn er über eine unverschlüsselte
Verbindung benutzt wird (im TrueNAS-Quellcode belegt: „Attempt to use over an
insecure transport“). Deshalb werden `http://` und `ws://` mit einer klaren
Fehlermeldung abgelehnt – im Assistenten und in den Systemdateien.

Das gilt auch über **Tailscale**: Tailscale verschlüsselt zwar selbst, aber
TrueNAS prüft, ob die Verbindung *bei ihm* per HTTPS ankommt. Deshalb immer
direkt den HTTPS-Port des TrueNAS verwenden (nicht über einen Proxy wie
`tailscale serve`, der per HTTP weiterreicht – ungeprüft, ob TrueNAS das als
unsicher wertet).

### Warum ein Fingerabdruck?

Ihr TrueNAS benutzt ein selbstsigniertes Zertifikat. Die übliche Prüfung
(„ist das Zertifikat von einer bekannten Stelle unterschrieben?“) schlägt
deshalb immer fehl. Viele Programme schalten die Prüfung dann einfach ab –
das wäre unsicher, denn dann könnte sich jedes Gerät im Netz als TrueNAS
ausgeben und den Key abgreifen.

Dieses Programm macht es anders: Es kennt den **SHA-256-Fingerabdruck**
(eine Art eindeutige Prüfsumme) jedes TrueNAS-Zertifikats und baut die
Verbindung **nur** auf, wenn er exakt übereinstimmt. Stimmt er nicht, wird
die Verbindung getrennt, **bevor** irgendetwas – erst recht nicht der Key –
gesendet wird.

### Fingerabdruck vergleichen

Der Assistent zeigt den Fingerabdruck an (Format `3F:A2:…:9C`, 32 Paare).
**Bewusst vergleichen**, bevor Sie bestätigen – ein Weg genügt:

- **A) Am sichersten – direkt auf dem TrueNAS** (unabhängig vom Netz):
  TrueNAS-Oberfläche → **System → Shell** (als Ihr normaler Admin), dann:
  ```sh
  sudo openssl x509 -in /etc/certificates/truenas_default.crt -noout -fingerprint -sha256
  ```
  Der Ordner `/etc/certificates/` ist im TrueNAS-Quellcode belegt;
  `truenas_default` ist der übliche Standardname (ungeprüft für Ihr System).
  Bei einem eigenen Zertifikat zeigt `sudo ls /etc/certificates/` die Namen.
- **B) Firefox:** TrueNAS-Seite öffnen (der Assistent bietet das an) →
  Schloss-Symbol links neben der Adresse → „Verbindung nicht sicher“ →
  „Weitere Informationen“ → „Zertifikat anzeigen“ → Abschnitt
  **„Fingerabdrücke“ → SHA-256**.
- **C) Chrome/Chromium/Brave:** Symbol links neben der Adresse → „Nicht
  sicher“ / „Zertifikat ist ungültig“ → Reiter **„Details“** →
  **SHA-256-Fingerabdruck**.
- Oder im Terminal Ihres PCs:
  ```sh
  openssl s_client -connect 192.168.1.20:443 </dev/null 2>/dev/null | openssl x509 -noout -fingerprint -sha256
  ```

Gross-/Kleinschreibung und Doppelpunkte spielen keine Rolle. B, C und der
Terminal-Befehl laufen über dasselbe Netz wie das Widget; im eigenen LAN oder
über Tailscale ist das praktisch ebenso gut. Weg A ist der einzige, den
niemand im Netz verfälschen kann.

**Nachträglich vergleichen** (falls Sie einfach bestätigt haben): Ihre
gespeicherten Werte zeigt
```sh
grep fingerprint ~/.config/truenas-widget/systems/*.toml
```
Mit A, B oder C vergleichen. Stimmt einer nicht, den API-Key dieses Systems
auf dem TrueNAS unter **My API Keys** löschen, im Assistenten
„Zertifikats-Fingerabdruck neu prüfen“ und einen neuen Key einrichten.

Ohne Assistent: `./diagnose.sh --nur-fingerabdruck --host 192.168.1.20 --port 443`
zeigt den Wert ebenfalls an (es wird dabei nichts gesendet).

### Wenn sich der Fingerabdruck ändert

Erneuern Sie das Zertifikat auf dem TrueNAS (oder läuft es ab und wird neu
erzeugt), ändert sich der Fingerabdruck. Das System zeigt dann „Zertifikats-
Fingerabdruck stimmt nicht überein“. Das ist **Absicht**: Es kann ein
harmloser Zertifikatswechsel sein – oder ein Angriff. Im Assistenten
**„Zertifikats-Fingerabdruck neu prüfen“** wählen: er zeigt alt und neu,
Sie vergleichen und bestätigen.

---

## 5. Bedienung, mehrere Systeme, Konfiguration, Deinstallation

### Prüfung manuell anstossen

- Knopf **„Aktualisieren“** in den Details (Desktop und aufgeklapptes
  Panel-Popup), oder
- **Rechtsklick → „Jetzt prüfen“**.

Beides löst dasselbe aus: „Prüfe…“, danach das neue Ergebnis aller Systeme
oder nach 150 s „Prüfung fehlgeschlagen“ (der bisherige Stand bleibt sichtbar).
Höchstens **eine Prüfung pro Minute** aus Widgets (auch bei Plasma-Neustart
oder mehreren Widgets). Bei zu frühem Klick erscheint für 10 Sekunden
„Bitte kurz warten – höchstens eine Prüfung pro Minute.“

Im Terminal geht es jederzeit ohne Sperre: `systemctl --user start truenas-widget.service`.

### Prüfung beim Start des Widgets

1. Das Widget liest beim Start `status.json`.
2. Ist die Datei **frisch** (jünger als `interval_minutes`): nichts tun.
3. Fehlt sie oder ist sie **älter**: **einmalig**
   `systemctl --user start truenas-widget.service` – derselbe Dienst, den
   auch der Timer startet.

Für die Minutensperre benutzt der Startbefehl zwei kleine Dateien in
`~/.cache/truenas-widget/` (`widget-trigger.stamp`, `widget-trigger.lock`).

### Mehrere Systeme und „offline ignorieren“

- Alle Systeme werden **gleichzeitig** geprüft; ein langsames oder
  unerreichbares System hält die anderen nicht auf.
- **Panel-Farbe** = schlimmster Zustand aller erreichbaren Systeme, plus:
  - Ein System, das **2 Prüfungen in Folge** nicht erreichbar war, während ein
    anderes antwortet, färbt das Panel **orange** (ein einzelner Aussetzer
    noch nicht).
  - Ein **falscher Fingerabdruck** färbt immer mindestens orange – das ist ein
    mögliches Sicherheitsproblem, kein normales „aus“.
  - Sind **alle** Systeme nicht erreichbar (z. B. unterwegs): grau.
  - Mit nur **einem** System gilt wie bisher: nicht erreichbar = grau.
- **„offline ignorieren“:** Rechtsklick auf das Widget → Häkchen bei
  „<name>: offline ignorieren“. Dann färbt dieses System das Panel nicht mehr,
  wenn es nicht erreichbar ist – praktisch für ein Remote-System über
  Tailscale, wenn Tailscale am Desktop nicht dauerhaft läuft. In den Details
  steht es trotzdem grau mit „nicht erreichbar“. Updates und Warnungen dieses
  Systems zählen weiterhin normal. Ein falscher Fingerabdruck lässt sich
  **nicht** ignorieren.
- Die Häkchen speichert Plasma **pro Widget**: Panel- und Desktop-Widget
  haben eigene Einstellungen.

### Wann geprüft wird (systemd-Timer)

Der Timer läuft nach der Uhr, z. B. bei 15 Minuten um xx:00, xx:15, xx:30
und xx:45 (`OnCalendar=`). Mit `Persistent=true` holt er eine verpasste
Prüfung sofort nach, wenn der PC aus war. Laut systemd-Dokumentation
(systemd.timer(5)) wirkt `Persistent=true` **nur** zusammen mit
`OnCalendar=`. Deshalb muss das Intervall glatt in eine Stunde bzw. einen Tag
passen (Liste unten).

### Konfiguration von Hand (optional)

Normalerweise nicht nötig – der Assistent erledigt das. Wer es trotzdem
selbst machen möchte:

**`~/.config/truenas-widget/config.toml`** (Vorlage:
[`config.example.toml`](config.example.toml)):

| Einstellung | Bedeutung |
|---|---|
| `[checker] interval_minutes` | Prüfabstand in Minuten (Standard 15). Erlaubt: 1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60, 120, 180, 240, 360, 480, 720, 1440. Nach einer Änderung `./install.sh` erneut ausführen. |
| `[notifications] enabled` | Benachrichtigungen an/aus |

**`~/.config/truenas-widget/systems/<kennung>.toml`** (Vorlage:
[`system.example.toml`](system.example.toml)):

| Einstellung | Bedeutung |
|---|---|
| `name` | Anzeigename im Widget |
| `host` | Adresse des TrueNAS, **ohne** `http://` |
| `port` | HTTPS-Port der Weboberfläche (z. B. 443) |
| `username` | Benutzer, dem der API-Key gehört |
| `fingerprint_sha256` | Zertifikats-Fingerabdruck (Abschnitt 4) |
| `api_version` | API-Version im Pfad, Standard `v25.10.0` |
| `timeout_seconds` | Wartezeit auf dieses TrueNAS (Standard 20; über Tailscale ggf. mehr) |
| `[key] source` | `"file"` (Standard, Datei `keys/<kennung>`) oder `"secret-tool"` |

Ein fehlerhaftes System legt die anderen nicht lahm: Es erscheint im Widget
grau mit der Fehlermeldung.

### Benachrichtigungen

Wann gibt es eine?

- bei einem **neuen** nicht quittierten Alert ab WARNING,
- bei einem **neuen** App-Update (App + neue Version),
- bei einem **neuen** Systemupdate (Version).

Der Titel nennt das System, z. B. „remote: App-Updates verfügbar“.
Abschalten in `config.toml`: `[notifications] enabled = false`. Der Prüfer
merkt sich trotzdem, was schon bekannt ist – beim Wieder-Einschalten wird
nichts „nachgeholt“. Ist ein System nicht erreichbar, gibt es dafür **keine**
Benachrichtigung.

### Nützliche Befehle

```sh
systemctl --user status truenas-widget.timer          # läuft der Timer? nächste Prüfung?
systemctl --user start truenas-widget.service         # jetzt sofort prüfen
journalctl --user -u truenas-widget.service -n 30     # letzte Meldungen des Prüfers
cat ~/.cache/truenas-widget/status.json               # aktuelles Ergebnis
./diagnose.sh                                         # Diagnose aller Systeme
./diagnose.sh --system remote                         # nur ein System
systemctl --user restart plasma-plasmashell.service   # Widget neu laden (ohne Abmelden)
```

### Deinstallation

```sh
./uninstall.sh
```

Entfernt Timer, Prüfer, Assistent, Menüeintrag, Widget, `status.json` und
Zustandsdateien. **Nicht** gelöscht werden Ihre Einstellungen, Systeme und
Keys in `~/.config/truenas-widget/` – das Skript zeigt an, wie Sie diese
selbst löschen. Vergessen Sie nicht, die API-Keys danach auf den TrueNAS-
Systemen (**My API Keys**) zu löschen.

---

## 6. Fehlersuche

Zuerst immer: `./diagnose.sh` ausführen und die Ausgabe lesen. Sie enthält
keinen Key, keine Adresse und keine Werte (nur die Kennungen Ihrer Systeme)
und darf weitergegeben werden.

| Anzeige / Meldung | Ursache | Lösung |
|---|---|---|
| Grau, „Nicht eingerichtet“ | Noch kein TrueNAS eingerichtet | Knopf „Einrichten…“ oder Rechtsklick → „TrueNAS hinzufügen/verwalten…“ |
| Assistent: „Zeitüberschreitung“ beim Test | TrueNAS antwortet zu langsam oder Tailscale-Verbindung noch im Aufbau | „Nochmal testen“; Einstellungen sind meist richtig |
| Assistent: „Es kam gar keine Verbindung zustande“ | Adresse/Port falsch, TrueNAS aus, Tailscale aus | Adresse prüfen, `tailscale status`, dann „Nochmal testen“ |
| Nichts passiert bei „TrueNAS hinzufügen/verwalten…“ | Starter fehlt (altes install.sh) oder kdialog fehlt | `./install.sh` erneut; `kdialog --version`; notfalls `./setup.sh --terminal` |
| System grau, „Nicht erreichbar“ | Anderes Netz, TrueNAS aus, Tailscale aus, falsche Adresse/Port | Im Browser `https://<host>:<port>` öffnen; Adresse im Assistenten prüfen |
| Panel orange, ein System „Offline“ | Dieses System fehlt seit mindestens 2 Prüfungen, ein anderes antwortet | Ursache wie oben – oder bewusst: Rechtsklick → „<name>: offline ignorieren“ |
| „Zertifikats-Fingerabdruck stimmt nicht überein“ | Zertifikat erneuert – oder ein fremdes Gerät | Assistent → „Zertifikats-Fingerabdruck neu prüfen“, **vergleichen** |
| „Anmeldung fehlgeschlagen (AUTH_ERR)“ | Benutzername oder Key falsch, Key gelöscht | Assistent → „API-Key erneuern“ bzw. Benutzer ändern |
| „Anmeldung fehlgeschlagen (EXPIRED)“ | Key abgelaufen **oder widerrufen** (z. B. weil er einmal über HTTP benutzt wurde) | Neuen Key erzeugen, Assistent → „API-Key erneuern“ |
| „Zugriff verweigert für …“ / „Daten unvollständig“ | Benutzer hat nicht die Rolle **Readonly Admin** | Abschnitt 2b. Diagnose zeigt, welche Abfrage betroffen ist |
| „API-Key nicht lesbar“ | Key-Datei fehlt/leer, oder KWallet gesperrt | Assistent → „API-Key erneuern“ |
| Warnung „Key-Datei hat zu offene Rechte“ | Andere Benutzer könnten den Key lesen | `chmod 600 <datei>` und `chmod 700 ~/.config/truenas-widget ~/.config/truenas-widget/keys` |
| „Konfigurationsfehler: …“ bei einem System | Datei in `systems/` von Hand falsch bearbeitet | Meldung lesen; im Assistenten „(fehlerhafte Datei): entfernen“ und neu anlegen |
| „config.toml hat noch das alte Format“ | Update von 0.3 ohne `install.sh` | `./install.sh` ausführen |
| Grau, „Veraltet“ | Prüfer läuft nicht (oder PC war im Ruhezustand) | `systemctl --user status truenas-widget.timer`, `journalctl --user -u truenas-widget.service -n 30` |
| Grau, „Noch keine Daten“ | Prüfer lief noch nie (oder status.json noch im alten Format) | „Aktualisieren“ klicken |
| „Prüfung fehlgeschlagen“ | Nach 150 s keine neuen Daten (Dienst nicht installiert, hängt, Fehler) | `systemctl --user status truenas-widget.service`, `journalctl …`; ggf. `./install.sh` |
| „Bitte kurz warten – höchstens eine Prüfung pro Minute.“ | Zu früh erneut geklickt | Eine Minute warten. Gewollter Schutz |
| Keine Häkchen „offline ignorieren“ im Menü | Nur ein System eingerichtet (dann nicht nötig) | – |
| „'interval_minutes' = … ist nicht möglich“ | Intervall passt nicht glatt in Stunde/Tag | Wert aus der Liste in Abschnitt 5 wählen, dann `./install.sh` |
| Keine Benachrichtigungen | abgeschaltet, `notify-send` fehlt, oder Ereignis schon gemeldet | `[notifications] enabled`, `sudo pacman -S libnotify` |
| Widget nicht in der Liste / altes Aussehen | Kein Plasma 6, Installation fehlgeschlagen oder altes Widget geladen | `plasmashell --version`, `./install.sh`, `systemctl --user restart plasma-plasmashell.service` |
| „WebSocket-Aufbau abgelehnt (HTTP 404)“ | API-Pfad passt nicht zur TrueNAS-Version | in der Systemdatei `api_version = "current"` probieren, Diagnose erneut |

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
offenen Rechten · Benachrichtigung nur einmal pro Ereignis und System ·
**mehrere Systeme** (parallel, eins offline, Zähler „2× in Folge“, kaputte
Systemdatei, fehlender Key) · Panel-Farbe mit „offline ignorieren“ ·
Fingerabdruck nie ignorierbar · **Assistent** (hinzufügen, http abgelehnt,
Fingerabdruck nicht bestätigt → nichts gesendet, falscher Key, fehlende
Rechte, KWallet über stdin, entfernen, Key erneuern, Fingerabdruck geändert,
umbenennen) · nachgebautes kdialog (Key nie in den Programm-Argumenten) ·
Umstellung von 0.3 · veraltete status.json · max. Alert-Zeilen · Prüfung beim
Widget-Start · höchstens ein Start pro Minute · Widget startet nur die zwei
erlaubten Befehle · Timer mit `OnCalendar` + `Persistent=true` ·
`install.sh` in einer Sandbox. Jeder Test prüft, dass der Key in keinem Log
auftaucht.

Vor jedem Commit: `./tools/check-secrets.sh` (sucht nach IP-Adressen,
E-Mail-Adressen, Fingerabdrücken und Schlüsseln in den Repo-Dateien).

---

## 8. Technische Details, Quellen und ungeprüfte Punkte

### Aufbau des Repos

```
truenas_widget/        Prüfer und Assistent (Python, nur Standardbibliothek)
  config.py            Konfiguration lesen/schreiben, http:// ablehnen
  keystore.py          Key aus Datei/secret-tool, Rechte-Warnung
  wsclient.py          WebSocket über TLS mit Fingerabdruck-Prüfung
  rpc.py               JSON-RPC mit Whitelist
  checker.py           Abfragen aller Systeme, Gesamtstatus, status.json
  notify.py            Benachrichtigungen, jedes Ereignis einmal
  setup.py             Einrichtungs-Assistent (kdialog / Terminal)
  migrate.py           Umstellung von 0.3
  diagnose.py          Diagnose
plasmoid/package/      Plasma-6-Widget (metadata.json, QML, logic.js, config/main.xml)
systemd/               Service + Timer (Vorlagen für install.sh)
tests/                 Unit- und Ende-zu-Ende-Tests mit Attrappe
install.sh, uninstall.sh, setup.sh, diagnose.sh
config.example.toml    Beispiel allgemeine Einstellungen
system.example.toml    Beispiel für ein System (nur Platzhalter)
```

### Warum keine WebSocket-Bibliothek?

Der Prüfer benutzt **nur die Python-Standardbibliothek** und einen kleinen
eigenen WebSocket-Client (ca. 300 Zeilen inkl. Kommentaren). Gründe: Auf
CachyOS/Arch soll man nicht mit `pip` ins System-Python installieren; ein
Zusatzpaket müsste extra gepflegt werden; und vor allem baut der eigene
Client die TLS-Verbindung selbst auf und kann so den Fingerabdruck prüfen,
**bevor** ein einziges Byte gesendet wird.

### Warum ein eigenes Assistenten-Programm statt Eingabe im Widget?

Das Widget läuft im Plasma-Prozess. Ein dort eingegebener Key würde durch
Plasma laufen und könnte nur im Klartext in Plasmas Einstellungen gespeichert
oder über eine Befehlszeile weitergegeben werden. Der Assistent ist dagegen
ein eigenes Programm: Der Key kommt aus dem verdeckten kdialog-Feld über
dessen Ausgabe direkt in die Datei mit Rechten 600 (oder per Standardeingabe
an `secret-tool`) – das Widget sieht ihn nie.

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
| Oberfläche | Benutzer-Menü → „My API Keys“ (`/credentials/users/api-keys`); Benutzerformular „TrueNAS Access“ + Rolle; „SMB Access“ standardmässig an | webui `user-menu.component.html`, `allowed-access-section` |

Die Schemas für Alerts, Apps, Updates und Anmeldung sind in allen
API-Versionen von v25.10.0 bis v25.10.5 identisch.

### Benutzte KDE-Bausteine (belegt im Quellcode)

| Was | Quelle |
|---|---|
| Befehle ausführen: „executable“-Datenquelle (`stdout`, `exit code`, über die Shell, kein eigener Timeout) | plasma-workspace 6.4, `dataengines/executable/executable.cpp` |
| Rechtsklick-Menü: `Plasmoid.contextualActions` mit `PlasmaCore.Action`, dynamisch per `Instantiator` + `splice` | kdeplasma-addons 6.4, `applets/timer` |
| Menüeinträge mit Häkchen (`checkable`) | kdeplasma-addons 6.4, `applets/comic` |
| Widget-Einstellungen `config/main.xml` (`StringList`) | kdeplasma-addons 6.4, `applets/timer` |
| Panel/Desktop unterscheiden über `Plasmoid.location` | kdeplasma-addons 6.4, `applets/webbrowser` |
| kdialog: `--inputbox`, `--password`, `--menu`, `--yesno` (`--yes-label`/`--no-label`), `--msgbox`, `--error`, `--title`; Eingabe über stdout, Exit 0/1 | KDE/kdialog, `src/kdialog.cpp` |

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

### UNGEPRÜFT – bitte am echten System bestätigen

Bereits auf dem echten System bestätigt (Version 0.3): Verbindung,
Fingerabdruck, Anmeldung und die drei Abfragen inkl. `select` bei
`app.query`; Widget im Panel und auf dem Desktop; „Jetzt prüfen“.

Noch offen:

1. **Felder bei echten Updates/Warnungen:** Ob „alt → neu“ bei App-Updates
   (`version → latest_version`) so aussieht wie in der TrueNAS-Oberfläche,
   zeigt sich erst beim ersten echten Update.
2. **Neu in 0.4, nur mit Attrappen und Tests geprüft:**
   - der Einrichtungs-Assistent mit dem echten kdialog (Fenster, Texte),
   - die dynamischen Häkchen „offline ignorieren“ im Rechtsklick-Menü,
   - das Starten des Assistenten aus dem Widget,
   - die Detailansicht mit mehreren Systemen.
3. **Tailscale:** direkte HTTPS-Verbindung zum TrueNAS über die
   Tailscale-Adresse (sollte wie im LAN funktionieren). Ungeprüft, wie TrueNAS
   reagiert, wenn ein Proxy (`tailscale serve`) per HTTP weiterreicht – nicht
   empfohlen.
4. **KWallet als Secret-Service** (Speicherort „KDE-Passwortspeicher“ im
   Assistenten). Ist der Schlüsselbund nach dem Anmelden gesperrt, kann der
   Prüfer den Key nicht lesen – dann die Datei-Variante nehmen.
5. **Benachrichtigungen** über `notify-send` aus dem systemd-User-Dienst.
6. **Bezeichnungen in der deutschen TrueNAS-Oberfläche** – hier stehen die
   englischen Originalnamen aus dem Quellcode.

### Lizenz

Noch keine Lizenz festgelegt. Bitte vor einer Veröffentlichung eine wählen.
