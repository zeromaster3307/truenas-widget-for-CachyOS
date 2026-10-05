# TrueNAS Status Widget for KDE Plasma 6 (CachyOS)

🇩🇪 [Deutsch](README.md) · 🇬🇧 English

A small widget for the Plasma desktop that shows at a glance whether
everything is fine on your TrueNAS – or several of them – or whether there
are **app updates**, a **system update** or **alerts**.

> Note: the widget's user interface and messages are in **German**. This
> README explains everything in English.

| Colour | Meaning |
|---|---|
| 🟢 green | everything OK |
| 🟡 yellow | updates available (apps or system) |
| 🟠 orange | warning – with several systems also: one system is repeatedly unreachable while another one responds |
| 🔴 red | critical alert |
| ⚪ grey | offline / unreachable / data outdated / not set up yet |

**Version 0.5.0** · Licence: [GNU GPL v3 or later](LICENSE)

**Assumptions** (please check):

- **TrueNAS Community Edition 25.10.x (Goldeye)**, web UI reachable via HTTPS
  (port 443 in the examples, self-signed certificate). A system that is only
  reachable via **Tailscale** works too, as long as the TrueNAS HTTPS web UI
  is addressed directly.
- **KDE Plasma 6**. Check with `plasmashell --version` – the output must start
  with `plasmashell 6.`. The widget does **not** work on Plasma 5.
- **Python 3.11 or newer** (preinstalled on CachyOS; check with
  `python3 --version`).
- **kdialog** for the windows of the setup assistant (check with
  `kdialog --version`, otherwise `sudo pacman -S kdialog`). Without kdialog
  the assistant asks its questions in the terminal.

---

## Contents

1. [What the project does – and deliberately does not do](#1-what-the-project-does--and-deliberately-does-not-do)
2. [Preparation on the TrueNAS: user, role, API key](#2-preparation-on-the-truenas-user-role-api-key)
3. [Install and set up a TrueNAS (assistant)](#3-install-and-set-up-a-truenas-assistant)
4. [HTTPS and certificate fingerprint](#4-https-and-certificate-fingerprint)
5. [Usage, several systems, configuration, uninstall](#5-usage-several-systems-configuration-uninstall)
6. [Troubleshooting](#6-troubleshooting)
7. [Tests (for developers)](#7-tests-for-developers)
8. [Technical details, sources and unverified points](#8-technical-details-sources-and-unverified-points)

---

## 1. What the project does – and deliberately does not do

### What it does

The project consists of four parts:

1. **Checker** (Python). Runs automatically every 15 minutes (configurable)
   via a systemd user timer and queries **all configured TrueNAS systems in
   parallel**:
   - **Alerts:** level, text, whether dismissed. Only **non-dismissed** alerts
     of level **WARNING** or higher count. Pure info messages (INFO, NOTICE)
     are ignored.
   - **Apps:** which installed apps have an update (name, current version →
     new version).
   - **System update:** yes/no and which version.
   - **Certificate:** when the TrueNAS certificate expires (warning 30 days
     ahead).

   The result is written to the small file `~/.cache/truenas-widget/status.json`.
   For **new** alerts, **new** updates and problems that need your action, a
   desktop notification is shown – each event only **once** (per system).
2. **Plasma widget.** Only reads that file. It **never** talks to TrueNAS
   itself and knows no API key.
   - **In the panel:** just a coloured circle (overall status of all systems);
     hovering shows a short summary per system. A click opens the details.
   - **On the desktop:** always shows the details directly, regardless of the
     widget's size.
   - **Details:** per system a coloured dot, name and status. If a system is
     fine, only its name and a green "OK" are shown. Otherwise just what
     matters below it (app updates, system update, alerts – up to 5 with one
     system, up to 3 per system with several, then "+ n weitere"). At the
     bottom, shared: **"Letzte Prüfung: hh:mm"** (last check) and the button
     **"Aktualisieren"** (refresh).
   - Clicking a system opens **its** TrueNAS web UI in the browser.
   - If the file is older than 45 minutes, the widget shows grey "Veraltet"
     (outdated).
   - **On start** (e.g. after login) the widget checks whether the data is
     older than the check interval and then triggers **one** check.
   - **Right-click menu:** "Jetzt prüfen" (check now), with several systems
     one "offline ignorieren" (ignore offline) checkbox per system, and
     "TrueNAS hinzufügen/verwalten…" (add/manage TrueNAS).
3. **Setup assistant.** Windows that guide you step by step through adding a
   TrueNAS (address, confirm fingerprint, enter key, test connection) – and
   through changing or removing systems, renewing a key and re-checking a
   fingerprint. No editing of configuration files needed.
4. **Diagnostic script.** Checks the connection, shows the certificate
   fingerprint and its expiry date and lists the **field names and data
   types** of the TrueNAS responses (no values). This allows comparing the
   fields against your real system.

### What it deliberately does NOT do

- **It changes nothing on the TrueNAS.** No installing updates, no reboots,
  no starting or stopping apps, no dismissing alerts. This is secured three
  times:
  1. The program has a **fixed list of allowed methods** (whitelist):
     `auth.login_ex` (login), `alert.list`, `app.query`, `update.status`.
     Every other call is refused **before** anything is sent. This also
     applies to the connection test in the assistant.
  2. The TrueNAS user only has a **read-only role**.
  3. The tests verify that write methods are refused.
- **No unencrypted HTTP.** `http://` and `ws://` are rejected.
- **Certificate checking is not switched off.** Instead the certificate's
  fingerprint is compared (section 4).
- **The widget never talks to TrueNAS and knows no key.** It only reads
  `status.json` and may start exactly **two** commands, both without
  parameters, key or address:
  1. `systemctl --user start truenas-widget.service` (the check),
  2. `~/.local/share/truenas-widget/setup.sh` (the setup assistant).
- **The API key is only entered in the assistant**, in a hidden field. From
  there it goes directly into a file with permissions 600 or into KWallet –
  never through the widget, never via a command line.
- **No alarm if a single TrueNAS is unreachable** (other network, switched
  off …). The widget simply turns grey. For several systems see section 5
  ("offline ignorieren").
- **The API key** never appears in the repository, logs, error messages or
  `status.json`.
- It does **not** use the old REST API (removed in TrueNAS 26.04) but the
  current JSON-RPC 2.0 interface over WebSocket.

---

## 2. Preparation on the TrueNAS: user, role, API key

This has to be done once **on every TrueNAS** you want to monitor. The
assistant (section 3) shows these steps again and opens the API keys page in
the browser.

> If you already have a user and key: skip this, but check that the user has
> the role **Readonly Admin**.

The labels come from the source code of the TrueNAS web UI, version 25.10.7.

### a) Create a user

1. In the TrueNAS web UI: **Credentials → Users → Add**.
2. **Username:** e.g. `widget-leser` (any name, remember it).
3. **Password:** long and random, e.g. created with `openssl rand -base64 32`
   on your PC. The password is never used – you don't need to remember it.
4. In the **Allow Access** area:
   - **SMB Access: UNTICK** (ticked by default!).
   - **Shell Access:** off.
   - **SSH Access:** off.
5. Save.

### b) "TrueNAS Access" with a read-only role

1. Edit the user (or do this while creating it).
2. Enable **TrueNAS Access**.
3. In the selection that appears (**Select Role**) choose **Readonly Admin**.
   *Not* "Full Admin" or "Sharing Admin".
4. Save.

The user form has **no** option for API keys. The UI itself says: "Access to
API can be granted after user has been created."

### c) Create the API key on the API keys page

1. Click the **user menu** (person icon) at the top right → **My API Keys**.
   (Page address: `https://<TrueNAS>:<port>/credentials/users/api-keys`)
2. Click **Add**.
3. **Name:** e.g. `plasma-widget`.
4. **Username:** select the user from a).
5. **Non-expiring** or an expiry date (**Expires On**) – your choice. When the
   key expires, the widget shows "Anmeldung fehlgeschlagen" (login failed)
   and you get one notification.
6. Save. **The key is shown only ONCE** – enter it in the assistant right
   away (section 3). If lost, simply create a new one and delete the old one.

> ⚠️ **Never** put the key into a chat, e-mail, Git repository, ticket or
> screenshot. Not even "just for testing".

---

## 3. Install and set up a TrueNAS (assistant)

### Install

```sh
# 1. Get the repository
mkdir -p ~/Projekte/Programme
cd ~/Projekte/Programme
git clone https://github.com/<YOUR-NAME>/truenas-widget-for-CachyOS.git
cd truenas-widget-for-CachyOS

# 2. Install (as normal user, NOT with sudo)
./install.sh
```

`install.sh` does the following (running it again is safe):

- copies checker and assistant to `~/.local/share/truenas-widget/` and adds
  the entry **"TrueNAS-Widget einrichten"** to the application menu,
- creates `~/.config/truenas-widget/` (permissions 700) with `config.toml`
  (general settings) – **only if none exists yet**,
- sets up the systemd user timer (`systemctl --user enable --now`) and
  triggers a first check in the background,
- installs or updates the Plasma widget (`kpackagetool6`).

Then add the widget: right-click on the panel or desktop → **Add Widgets…** →
search for **TrueNAS-Status** → drag it in.

### Set up a TrueNAS

Start the assistant – three equivalent ways:

- **Right-click the widget → "TrueNAS hinzufügen/verwalten…"**
  (as long as nothing is set up, also via the **"Einrichten…"** button)
- Application menu → **"TrueNAS-Widget einrichten"**
- Terminal in the repository folder: `./setup.sh` (or `./setup.sh --terminal`
  without windows)

The assistant asks, one after another:

1. **Display name** (e.g. `homelab` or `remote`).
2. **Address** (IP or name, e.g. `192.168.1.20` or `nas.tailnet.ts.net`) –
   `http://` is rejected.
3. **Port** (usually `443`).
4. **Fingerprint:** shows the SHA-256 fingerprint of the certificate together
   with instructions where to compare it (TrueNAS shell, Firefox, Chrome –
   see section 4) and opens the TrueNAS page in the browser if you like.
   Only after your **"Ja, stimmt überein"** (yes, it matches) does it
   continue – nothing is sent before that.
5. **Username** on the TrueNAS (default `widget-leser`).
6. Optionally opens the **My API Keys** page of this TrueNAS.
7. **Where to store the key** (only if `secret-tool` is available): file
   (default) or KDE password storage/KWallet.
8. **API key** in a hidden field.
9. **Connection test** (login + the three read queries, waiting up to 45 s).
   The test uses the same code as the regular check – it adds no extra
   security but gives immediate feedback:
   - **Wrong key or user:** enter the key again right away (it is not saved
     that way).
   - **Timeout / no connection:** the assistant says whether no connection
     was established at all or the TrueNAS just answered too slowly, names
     possible reasons (e.g. Tailscale) and offers **"Nochmal testen"** (test
     again) or **"Trotzdem speichern"** (save anyway).
   - **Missing permissions:** hint about the role "Readonly Admin", then
     "test again" or "save anyway".
10. Saves everything and triggers a check – the system shows up in the widget
    after a few seconds.

If a system is already set up, the assistant shows a menu: **add new
TrueNAS**, and per system **change name/address/user**, **renew API key**,
**re-check certificate fingerprint**, **remove**.

### Where things are stored

| File | Content |
|---|---|
| `~/.config/truenas-widget/config.toml` | general settings (interval, notifications) |
| `~/.config/truenas-widget/systems/<id>.toml` | one TrueNAS: name, address, port, user, fingerprint – **no key** (permissions 600) |
| `~/.config/truenas-widget/keys/<id>` | this system's API key (permissions 600, folder 700) – or KWallet instead |

The assistant derives `<id>` from the display name (e.g. "Mein NAS" →
`mein-nas`). It stays the same when renaming.

### Update

```sh
cd ~/Projekte/Programme/truenas-widget-for-CachyOS
git pull
./install.sh
systemctl --user restart plasma-plasmashell.service   # only needed if the widget changed
```

---

## 4. HTTPS and certificate fingerprint

### Why no HTTP?

TrueNAS **revokes an API key immediately** if it is used over an unencrypted
connection (verified in the TrueNAS source code: "Attempt to use over an
insecure transport"). That is why `http://` and `ws://` are rejected with a
clear error message – in the assistant and in the system files.

This also applies over **Tailscale**: Tailscale encrypts by itself, but
TrueNAS checks whether the connection arrives *at TrueNAS* via HTTPS. So
always use the TrueNAS HTTPS port directly (not via a proxy such as
`tailscale serve` that forwards via HTTP – unverified whether TrueNAS treats
that as insecure).

### Why a fingerprint?

Your TrueNAS uses a self-signed certificate. The usual check ("is the
certificate signed by a known authority?") therefore always fails. Many
programs then simply switch checking off – that would be insecure, because
any device in the network could pretend to be the TrueNAS and grab the key.

This program does it differently: it knows the **SHA-256 fingerprint** (a
kind of unique checksum) of each TrueNAS certificate and only connects if it
matches exactly. If it doesn't match, the connection is dropped **before**
anything – let alone the key – is sent.

### Comparing the fingerprint

The assistant shows the fingerprint (format `3F:A2:…:9C`, 32 pairs).
**Compare consciously** before confirming – one way is enough:

- **A) Safest – directly on the TrueNAS** (independent of the network):
  TrueNAS web UI → **System → Shell** (as your normal admin), then:
  ```sh
  sudo openssl x509 -in /etc/certificates/truenas_default.crt -noout -fingerprint -sha256
  ```
  The folder `/etc/certificates/` is verified in the TrueNAS source code;
  `truenas_default` is the usual default name (unverified for your system).
  With your own certificate, `sudo ls /etc/certificates/` shows the names.
- **B) Firefox:** open the TrueNAS page (the assistant offers this) → lock
  icon left of the address → "Connection not secure" → "More information" →
  "View Certificate" → section **"Fingerprints" → SHA-256**.
- **C) Chrome/Chromium/Brave:** icon left of the address → "Not secure" /
  "Certificate is not valid" → tab **"Details"** → **SHA-256 fingerprint**.
- Or in a terminal on your PC:
  ```sh
  openssl s_client -connect 192.168.1.20:443 </dev/null 2>/dev/null | openssl x509 -noout -fingerprint -sha256
  ```

Upper/lower case and colons don't matter. B, C and the terminal command use
the same network as the widget; in your own LAN or via Tailscale that is
practically just as good. Way A is the only one nobody in the network can
tamper with.

**Comparing afterwards** (if you just confirmed): your stored values are
shown by
```sh
grep fingerprint ~/.config/truenas-widget/systems/*.toml
```
Compare with A, B or C. If one does not match, delete that system's API key
on the TrueNAS under **My API Keys**, choose "re-check certificate
fingerprint" in the assistant and set up a new key.

### Warning before the certificate expires

Self-signed TrueNAS certificates expire at some point and are renewed – then
the fingerprint changes. So that this doesn't come as a surprise, the checker
reads the expiry date on every connection:

- **From 30 days before expiry** an orange hint line appears for the system
  (even if everything else is "OK") and in the tooltip, e.g. "Zertifikat
  läuft in 12 Tagen ab (31.10.2026)". The panel colour does **not** change.
- In addition, **one** desktop notification – and one more if it actually
  expires.
- `./diagnose.sh` shows "Zertifikat gültig bis" (valid until).

### When the fingerprint changes

When the certificate on the TrueNAS is renewed (or expires and is
regenerated), the fingerprint changes. The system then shows "Zertifikats-
Fingerabdruck stimmt nicht überein" (fingerprint does not match). This is
**intended**: it may be a harmless certificate change – or an attack. In the
assistant choose **"Zertifikats-Fingerabdruck neu prüfen"**: it shows old
and new, you compare and confirm.

---

## 5. Usage, several systems, configuration, uninstall

### Triggering a check manually

- Button **"Aktualisieren"** in the details (desktop and expanded panel
  popup), or
- **right-click → "Jetzt prüfen"**.

Both do the same: "Prüfe…" (checking), then the new result of all systems,
or after 150 s "Prüfung fehlgeschlagen" (check failed; the previous state
stays visible). At most **one check per minute** from widgets (also across a
Plasma restart or several widgets). If you click too early, "Bitte kurz
warten – höchstens eine Prüfung pro Minute." is shown for 10 seconds.

In a terminal it always works without the limit:
`systemctl --user start truenas-widget.service`.

### Check on widget start

1. On start the widget reads `status.json`.
2. If the file is **fresh** (younger than `interval_minutes`): do nothing.
3. If it is missing or **older**: **once**
   `systemctl --user start truenas-widget.service` – the same service the
   timer starts.

For the one-minute limit the start command uses two small files in
`~/.cache/truenas-widget/` (`widget-trigger.stamp`, `widget-trigger.lock`).

### Several systems and "offline ignorieren"

- All systems are checked **in parallel**; a slow or unreachable system does
  not hold up the others.
- **Panel colour** = worst state of all reachable systems, plus:
  - A system that was unreachable **in 2 consecutive checks** while another
    one responds turns the panel **orange** (a single dropout doesn't yet).
  - A **wrong fingerprint** always gives at least orange – that is a
    potential security problem, not a normal "off".
  - If **all** systems are unreachable (e.g. on the road): grey.
  - With only **one** system: unreachable = grey, as before.
- **"offline ignorieren" (ignore offline):** right-click the widget → tick
  "<name>: offline ignorieren". This system then no longer colours the panel
  when it is unreachable – handy for a remote system via Tailscale if
  Tailscale doesn't run permanently on the desktop. In the details it is
  still shown grey with "nicht erreichbar". Updates and alerts of this system
  still count normally. A wrong fingerprint can **not** be ignored.
- Plasma stores the ticks **per widget**: panel and desktop widget have their
  own settings.

The colour rule exists both in the checker (Python) and in the widget
(JavaScript); both are tested with the same cases
(`tests/aggregate_cases.json`).

### When checks run (systemd timer)

The timer runs by the clock, e.g. with 15 minutes at xx:00, xx:15, xx:30 and
xx:45 (`OnCalendar=`). With `Persistent=true` it catches up on a missed check
immediately if the PC was off. According to the systemd documentation
(systemd.timer(5)) `Persistent=true` **only** works together with
`OnCalendar=`. Therefore the interval must divide an hour or a day evenly
(list below).

### Manual configuration (optional)

Usually not needed – the assistant does this. If you want to do it yourself:

**`~/.config/truenas-widget/config.toml`** (template:
[`config.example.toml`](config.example.toml)):

| Setting | Meaning |
|---|---|
| `[checker] interval_minutes` | check interval in minutes (default 15). Allowed: 1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60, 120, 180, 240, 360, 480, 720, 1440. Run `./install.sh` again after changing it. |
| `[notifications] enabled` | notifications on/off |

**`~/.config/truenas-widget/systems/<id>.toml`** (template:
[`system.example.toml`](system.example.toml)):

| Setting | Meaning |
|---|---|
| `name` | display name in the widget |
| `host` | address of the TrueNAS, **without** `http://` |
| `port` | HTTPS port of the web UI (e.g. 443) |
| `username` | user the API key belongs to |
| `fingerprint_sha256` | certificate fingerprint (section 4) |
| `api_version` | API version in the path, default `v25.10.0` |
| `timeout_seconds` | how long to wait for this TrueNAS (default 20; possibly more via Tailscale) |
| `[key] source` | `"file"` (default, file `keys/<id>`) or `"secret-tool"` |

A broken system file does not affect the others: that system appears grey in
the widget with the error message.

### Notifications

When is one shown? **Once** per system for:

- a **new** non-dismissed alert of level WARNING or higher,
- a **new** app update (app + new version),
- a **new** system update (version),
- problems **that don't go away by themselves** and need your action:
  - login failed (API key expired or revoked),
  - API key not readable,
  - configuration error in a system file,
  - missing permissions (role "Readonly Admin" missing),
  - certificate expiring soon or expired.

  If the problem is fixed and occurs again later, you get another
  notification.

The title names the system, e.g. "remote: App-Updates verfügbar". Switch off
in `config.toml`: `[notifications] enabled = false`. The checker still
remembers what is already known – nothing is "caught up" when switching it
back on. "Unreachable" and "fingerprint does not match" deliberately trigger
**no** notification.

### Useful commands

```sh
systemctl --user status truenas-widget.timer          # is the timer running? next check?
systemctl --user start truenas-widget.service         # check right now
journalctl --user -u truenas-widget.service -n 30     # last messages of the checker
cat ~/.cache/truenas-widget/status.json               # current result
./diagnose.sh                                         # diagnose all systems
./diagnose.sh --system remote                         # one system only
systemctl --user restart plasma-plasmashell.service   # reload the widget (without logging out)
```

### Uninstall

```sh
./uninstall.sh
```

Removes timer, checker, assistant, menu entry, widget, `status.json` and
state files. Your settings, systems and keys in `~/.config/truenas-widget/`
are **not** deleted – the script tells you how to delete them yourself.
Don't forget to delete the API keys on the TrueNAS systems afterwards
(**My API Keys**).

---

## 6. Troubleshooting

First always run `./diagnose.sh` and read the output. It contains no key, no
address and no values (only the ids of your systems) and may be shared.

| Display / message | Cause | Solution |
|---|---|---|
| Grey, "Nicht eingerichtet" (not set up) | No TrueNAS set up yet | "Einrichten…" button or right-click → "TrueNAS hinzufügen/verwalten…" |
| Assistant: "Zeitüberschreitung" (timeout) during the test | TrueNAS answers too slowly or the Tailscale connection is still being established | "Nochmal testen"; the settings are usually correct |
| Assistant: "Es kam gar keine Verbindung zustande" (no connection at all) | Wrong address/port, TrueNAS off, Tailscale off | Check address, `tailscale status`, then "Nochmal testen" |
| Nothing happens on "TrueNAS hinzufügen/verwalten…" | Launcher missing or kdialog missing | Run `./install.sh` again; `kdialog --version`; if needed `./setup.sh --terminal` |
| System grey, "Nicht erreichbar" (unreachable) | Other network, TrueNAS off, Tailscale off, wrong address/port | Open `https://<host>:<port>` in the browser; check the address in the assistant |
| Panel orange, one system "Offline" | This system has been missing for at least 2 checks while another responds | Cause as above – or deliberately: right-click → "<name>: offline ignorieren" |
| "Zertifikats-Fingerabdruck stimmt nicht überein" | Certificate renewed – or a foreign device | Assistant → "Zertifikats-Fingerabdruck neu prüfen", **compare** |
| Orange hint "Zertifikat läuft in … Tagen ab" | The TrueNAS certificate expires soon | Nothing to do until it is renewed. Afterwards in the assistant "Zertifikats-Fingerabdruck neu prüfen" |
| "Anmeldung fehlgeschlagen (AUTH_ERR)" | Username or key wrong, key deleted | Assistant → "API-Key erneuern" or change user |
| "Anmeldung fehlgeschlagen (EXPIRED)" | Key expired **or revoked** (e.g. because it was once used over HTTP) | Create a new key, assistant → "API-Key erneuern" |
| "Zugriff verweigert für …" / "Daten unvollständig" | User doesn't have the role **Readonly Admin** | Section 2b. The diagnosis shows which query is affected |
| "API-Key nicht lesbar" | Key file missing/empty, or KWallet locked | Assistant → "API-Key erneuern" |
| Warning "Key-Datei hat zu offene Rechte" (permissions too open) | Other users could read the key | `chmod 600 <file>` and `chmod 700 ~/.config/truenas-widget ~/.config/truenas-widget/keys` |
| "Konfigurationsfehler: …" for a system | File in `systems/` edited incorrectly by hand | Read the message; in the assistant "(fehlerhafte Datei): entfernen" and create it again |
| Grey, "Veraltet" (outdated) | Checker not running (or PC was suspended) | `systemctl --user status truenas-widget.timer`, `journalctl --user -u truenas-widget.service -n 30` |
| Grey, "Noch keine Daten" (no data yet) | Checker never ran | Click "Aktualisieren" |
| "Prüfung fehlgeschlagen" (check failed) | No new data after 150 s (service not installed, hangs, error) | `systemctl --user status truenas-widget.service`, `journalctl …`; possibly `./install.sh` |
| "Bitte kurz warten – höchstens eine Prüfung pro Minute." | Clicked again too early | Wait a minute. Intentional protection |
| No "offline ignorieren" ticks in the menu | Only one system set up (not needed then) | – |
| "'interval_minutes' = … ist nicht möglich" | Interval doesn't divide an hour/day evenly | Pick a value from the list in section 5, then `./install.sh` |
| No notifications | Switched off, `notify-send` missing, or event already notified | `[notifications] enabled`, `sudo pacman -S libnotify` |
| Widget not in the list / old look | Not Plasma 6, installation failed or old widget loaded | `plasmashell --version`, `./install.sh`, `systemctl --user restart plasma-plasmashell.service` |
| "WebSocket-Aufbau abgelehnt (HTTP 404)" | API path doesn't match the TrueNAS version | try `api_version = "current"` in the system file, run the diagnosis again |

---

## 7. Tests (for developers)

```sh
python3 -m unittest -v
```

The tests need no TrueNAS: they use mock responses and a small test server
(real TLS + WebSocket on `127.0.0.1` with a test certificate generated on
every run; requires `openssl`). The widget logic is tested with Node.js
(skipped if not installed). Covered among others:

all OK · app updates · system update · warning · critical · dismissed alert
(ignored) · info alert (ignored) · unreachable · wrong fingerprint (nothing is
sent) · missing permissions · whitelist refuses write methods · `http://` is
rejected · key file with too open permissions · notification only once per
event and system · **several systems** (parallel, one offline, "2× in a row"
counter, broken system file, missing key) · panel colour with "offline
ignorieren" · fingerprint never ignorable · **assistant** (add, http rejected,
fingerprint not confirmed → nothing sent, wrong key, missing permissions,
timeout → test again / save anyway, KWallet via stdin, remove, renew key,
changed fingerprint, rename) · replica of kdialog (key never in the program
arguments) · certificate expiry date (checked against openssl) and warning ·
one-time notification for expired key, missing permissions etc. · shared
test cases for the panel colour in checker and widget
(`tests/aggregate_cases.json`) · outdated status.json · max. alert lines ·
check on widget start · at most one start per minute · widget starts only the
two allowed commands · timer with `OnCalendar` + `Persistent=true` ·
`install.sh` in a sandbox. Every test verifies that the key never appears in
any log.

Before every commit: `./tools/check-secrets.sh` (searches the repository
files for IP addresses, e-mail addresses, fingerprints and keys).

---

## 8. Technical details, sources and unverified points

### Repository layout

```
truenas_widget/        checker and assistant (Python, standard library only)
  config.py            read/write configuration, reject http://
  keystore.py          key from file/secret-tool, permission warning
  wsclient.py          WebSocket over TLS with fingerprint check, certificate expiry
  rpc.py               JSON-RPC with whitelist
  checker.py           queries for all systems, overall status, status.json
  notify.py            notifications, each event once
  setup.py             setup assistant (kdialog / terminal)
  diagnose.py          diagnosis
plasmoid/package/      Plasma 6 widget (metadata.json, QML, logic.js, config/main.xml)
systemd/               service + timer (templates for install.sh)
tests/                 unit and end-to-end tests with a mock TrueNAS
tests/aggregate_cases.json  shared test cases for the panel colour (checker + widget)
install.sh, uninstall.sh, setup.sh, diagnose.sh
config.example.toml    example general settings
system.example.toml    example for one system (placeholders only)
LICENSE                GNU GPL v3
README.md, README.en.md  documentation German / English
```

### Why no WebSocket library?

The checker uses **only the Python standard library** and a small WebSocket
client of its own (about 300 lines including comments). Reasons: on
CachyOS/Arch you shouldn't install into the system Python with `pip`; an
extra package would need maintaining; and above all the own client sets up
the TLS connection itself and can therefore check the fingerprint **before**
a single byte is sent.

### Why a separate assistant program instead of input in the widget?

The widget runs inside the Plasma process. A key entered there would pass
through Plasma and could only be stored in plain text in Plasma's settings
or passed on via a command line. The assistant, in contrast, is a separate
program: the key goes from the hidden kdialog field via its output directly
into the file with permissions 600 (or via standard input to `secret-tool`) –
the widget never sees it.

### TrueNAS interface used (verified)

The documentation page <https://api.truenas.com/v25.10> was **not reachable**
from the development environment (network block). Everything below was
therefore verified directly in the **TrueNAS source code, version 25.10.7**
(github.com/truenas/middleware and github.com/truenas/webui, tag
`TS-25.10.7`). The documentation is generated from this source code.

| What | Value | Source in the code |
|---|---|---|
| Endpoint | `wss://<host>:<port>/api/v25.10.0` (also `/api/current`) | `middlewared/main.py`: route `/api/{version}` |
| Login | `auth.login_ex` with `{"mechanism": "API_KEY_PLAIN", "username", "api_key"}`, response `response_type` = `SUCCESS` | `api/v25_10_0/auth.py` |
| Key revocation over HTTP | yes, "Attempt to use over an insecure transport" | `plugins/auth.py` |
| Alerts | `alert.list` → fields `uuid`, `level`, `dismissed`, `text`, `formatted`, … | `api/v25_10_0/alert.py`, role `ALERT_LIST_READ` |
| Alert levels | INFO, NOTICE, WARNING, ERROR, CRITICAL, ALERT, EMERGENCY | `api/v25_10_0/alert.py` (`AlertLevel`) |
| Apps | `app.query` → `name`, `version`, `human_version`, `latest_version`, `upgrade_available`, `image_updates_available`, `custom_app` | `api/v25_10_0/app.py`, role `APPS_READ` |
| System update | `update.status` → `code`, `status.new_version.version`, `error` | `api/v25_10_0/update.py`, role `SYSTEM_UPDATE_READ` |
| Missing permissions | error with `errname` = `EACCES`, "Not authorized" | `middlewared/main.py` |
| Role "Readonly Admin" | contains all `*_READ` roles | `middlewared/role.py` |
| Certificate files | `/etc/certificates/<name>.crt` | `plugins/crypto_/utils.py`, `query_utils.py` |
| UI | user menu → "My API Keys" (`/credentials/users/api-keys`); user form "TrueNAS Access" + role; "SMB Access" ticked by default | webui `user-menu.component.html`, `allowed-access-section` |

The schemas for alerts, apps, updates and login are identical in all API
versions from v25.10.0 to v25.10.5.

### KDE building blocks used (verified in source code)

| What | Source |
|---|---|
| Running commands: "executable" data source (`stdout`, `exit code`, via the shell, no own timeout) | plasma-workspace 6.4, `dataengines/executable/executable.cpp` |
| Right-click menu: `Plasmoid.contextualActions` with `PlasmaCore.Action`, dynamic via `Instantiator` + `splice` | kdeplasma-addons 6.4, `applets/timer` |
| Checkable menu entries (`checkable`) | kdeplasma-addons 6.4, `applets/comic` |
| Widget settings `config/main.xml` (`StringList`) | kdeplasma-addons 6.4, `applets/timer` |
| Distinguishing panel/desktop via `Plasmoid.location` | kdeplasma-addons 6.4, `applets/webbrowser` |
| kdialog: `--inputbox`, `--password`, `--menu`, `--yesno` (`--yes-label`/`--no-label`), `--msgbox`, `--error`, `--title`; input via stdout, exit 0/1 | KDE/kdialog, `src/kdialog.cpp` |

### Interpretations of the TrueNAS data

- **Alert levels:** TrueNAS knows more than "warning/critical". Mapping here:
  INFO and NOTICE → ignored; WARNING → **warning**; ERROR, CRITICAL, ALERT,
  EMERGENCY → **critical**. Unknown levels are treated like WARNING to be
  safe.
- **Alert text:** the field `text` is often just a template with
  placeholders (e.g. `%(pool)s`). Therefore the field `formatted` (HTML
  removed) is shown, otherwise `text`.
- **"Update check"** is called `update.status` in 25.10 (there is no
  `update.check_available` any more).
- **Login** is not a read method, but necessarily on the whitelist. It
  changes nothing on the system.
- **App updates:** `upgrade_available` **or** `image_updates_available`
  counts as an update (custom apps often only get a new container image;
  then "neues Image" is shown instead of a version number).

### UNVERIFIED – please confirm on the real system

Already confirmed on a real system: connection, fingerprint, login and the
three queries including `select` for `app.query`; widget in panel and on the
desktop; "Jetzt prüfen"/"Aktualisieren"; several systems (LAN + Tailscale);
setup assistant with kdialog.

Still open:

1. **Fields for real updates/alerts:** whether "old → new" for app updates
   (`version → latest_version`) looks like in the TrueNAS UI only shows with
   the first real update.
2. **New in 0.5, only checked with mocks and tests:** warning before
   certificate expiry (only shows ~30 days before expiry) and the one-time
   notifications for persistent problems.
3. **"offline ignorieren" ticks** in the right-click menu on a real desktop.
4. **Tailscale:** direct HTTPS connection to the TrueNAS via the Tailscale
   address (should work like in the LAN). Unverified how TrueNAS reacts when
   a proxy (`tailscale serve`) forwards via HTTP – not recommended.
5. **KWallet as secret service** (storage option "KDE password storage" in
   the assistant). If the wallet is locked after login, the checker cannot
   read the key – then use the file option.
6. **Notifications** via `notify-send` from the systemd user service.

### Licence

This project is licensed under the **GNU General Public License, version 3
or later** (GPL-3.0-or-later). See [LICENSE](LICENSE) for the full text. In
short: you may use, modify and share it; if you share a modified version, it
must also be under the GPL and come with source code. There is no warranty.
