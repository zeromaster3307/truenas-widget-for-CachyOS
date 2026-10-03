#!/bin/sh
# Installiert das TrueNAS-Status-Widget für den AKTUELLEN Benutzer (kein root nötig).
#
# Was passiert:
#   1. Prüft, ob Python 3.11 oder neuer vorhanden ist.
#   2. Kopiert den Prüfer nach ~/.local/share/truenas-widget/
#   3. Legt ~/.config/truenas-widget/ an (Rechte 700) und kopiert die
#      Beispiel-Konfiguration dorthin, falls noch keine config.toml existiert.
#   4. Richtet den systemd-User-Timer ein (Intervall aus der Konfiguration).
#   5. Installiert bzw. aktualisiert das Plasma-6-Widget (kpackagetool6).
#
# Der API-Key wird NICHT angefasst - den legen Sie selbst ab (siehe README).
# Erneut ausführen ist gefahrlos (z. B. nach Änderung des Intervalls oder Update).
set -eu

REPO=$(cd "$(dirname "$0")" && pwd)
APPDIR="${XDG_DATA_HOME:-$HOME/.local/share}/truenas-widget"
UNITDIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
CONFDIR="${XDG_CONFIG_HOME:-$HOME/.config}/truenas-widget"
PLASMOID_ID="local.truenasstatus"

say() { printf '%s\n' "$*"; }

if [ "$(id -u)" -eq 0 ]; then
    say "Bitte NICHT als root ausführen, sondern als normaler Benutzer."
    exit 1
fi

# --- 1. Python prüfen ---
PYTHON=$(command -v python3 || true)
if [ -z "$PYTHON" ] || ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    say "FEHLER: Python 3.11 oder neuer wird benötigt (Paket 'python')."
    exit 1
fi
say "[1/5] Python gefunden: $("$PYTHON" --version)"

# --- 2. Prüfer kopieren ---
mkdir -p "$APPDIR"
rm -rf "$APPDIR/truenas_widget"
cp -r "$REPO/truenas_widget" "$APPDIR/"
find "$APPDIR" -name '__pycache__' -type d -prune -exec rm -rf {} +
say "[2/5] Prüfer installiert nach $APPDIR"

# --- 3. Konfiguration ---
mkdir -p "$CONFDIR"
chmod 700 "$CONFDIR"
if [ ! -f "$CONFDIR/config.toml" ]; then
    cp "$REPO/config.example.toml" "$CONFDIR/config.toml"
    chmod 600 "$CONFDIR/config.toml"
    say "[3/5] Beispiel-Konfiguration angelegt: $CONFDIR/config.toml  -> BITTE ANPASSEN"
else
    say "[3/5] Vorhandene Konfiguration bleibt unverändert: $CONFDIR/config.toml"
fi
if [ -f "$CONFDIR/api-key" ]; then
    perms=$(stat -c '%a' "$CONFDIR/api-key")
    if [ "$perms" != "600" ] && [ "$perms" != "400" ]; then
        say "      WARNUNG: $CONFDIR/api-key hat Rechte $perms. Bitte: chmod 600 \"$CONFDIR/api-key\""
    fi
fi

# --- 4. systemd-Timer ---
INTERVAL=$(PYTHONPATH="$APPDIR" "$PYTHON" -m truenas_widget.config --print-interval)
ONCALENDAR=$(PYTHONPATH="$APPDIR" "$PYTHON" -m truenas_widget.config --print-oncalendar)
mkdir -p "$UNITDIR"
sed -e "s|@APPDIR@|$APPDIR|g" -e "s|@PYTHON@|$PYTHON|g" \
    "$REPO/systemd/truenas-widget.service" > "$UNITDIR/truenas-widget.service"
sed -e "s|@ONCALENDAR@|$ONCALENDAR|g" \
    "$REPO/systemd/truenas-widget.timer" > "$UNITDIR/truenas-widget.timer"
systemctl --user daemon-reload
# Timer dauerhaft einschalten UND sofort starten:
systemctl --user enable --now truenas-widget.timer
# War der Timer schon aktiv, übernimmt erst ein Neustart ein geändertes Intervall.
systemctl --user restart truenas-widget.timer
# Einmal sofort prüfen (im Hintergrund), damit das Widget gleich Daten hat.
systemctl --user start --no-block truenas-widget.service
say "[4/5] systemd-Timer aktiv (alle $INTERVAL Minuten)"

# --- 5. Plasma-Widget ---
if command -v kpackagetool6 >/dev/null 2>&1; then
    # Erst "aktualisieren" versuchen, sonst neu installieren.
    if kpackagetool6 -t Plasma/Applet -u "$REPO/plasmoid/package" >/dev/null 2>&1; then
        say "[5/5] Plasma-Widget aktualisiert ($PLASMOID_ID)"
    elif kpackagetool6 -t Plasma/Applet -i "$REPO/plasmoid/package" >/dev/null; then
        say "[5/5] Plasma-Widget installiert ($PLASMOID_ID)"
    else
        say "[5/5] FEHLER beim Installieren des Plasma-Widgets (siehe Meldung oben)."
    fi
else
    say "[5/5] kpackagetool6 nicht gefunden - Plasma-Widget NICHT installiert."
    say "      Ist das wirklich Plasma 6? Prüfen mit: plasmashell --version"
fi

say ""
say "Fertig. Nächste Schritte (Details im README):"
say "  - Konfiguration anpassen:     $CONFDIR/config.toml"
say "  - API-Key ablegen:            $CONFDIR/api-key (Rechte 600)"
say "  - Diagnose ausführen:         $REPO/diagnose.sh"
say "  - Sofort einmal prüfen:       systemctl --user start truenas-widget.service"
say "  - Widget hinzufügen:          Rechtsklick auf Panel/Desktop -> Widgets hinzufügen -> 'TrueNAS-Status'"
