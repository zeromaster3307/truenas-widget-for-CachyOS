#!/bin/sh
# Entfernt das TrueNAS-Status-Widget wieder.
#
# Entfernt: systemd-Timer und -Service, den installierten Prüfer, das
# Plasma-Widget, status.json und die Zustandsdatei.
# Bleibt erhalten: Ihre Konfiguration und der API-Key in
# ~/.config/truenas-widget/ (bewusst - löschen Sie diese selbst, siehe unten).
set -u

APPDIR="${XDG_DATA_HOME:-$HOME/.local/share}/truenas-widget"
UNITDIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
CONFDIR="${XDG_CONFIG_HOME:-$HOME/.config}/truenas-widget"
CACHEDIR="${XDG_CACHE_HOME:-$HOME/.cache}/truenas-widget"
STATEDIR="${XDG_STATE_HOME:-$HOME/.local/state}/truenas-widget"
PLASMOID_ID="local.truenasstatus"

systemctl --user disable --now truenas-widget.timer 2>/dev/null
# Zeitstempel-Datei von Persistent=true entfernen (empfohlen in systemd.timer(5))
systemctl --user clean --what=state truenas-widget.timer 2>/dev/null
systemctl --user stop truenas-widget.service 2>/dev/null
rm -f "$UNITDIR/truenas-widget.timer" "$UNITDIR/truenas-widget.service"
systemctl --user daemon-reload 2>/dev/null
echo "systemd-Timer entfernt."

rm -rf "$APPDIR" "$CACHEDIR" "$STATEDIR"
echo "Prüfer, status.json und Zustandsdatei entfernt."

if command -v kpackagetool6 >/dev/null 2>&1; then
    kpackagetool6 -t Plasma/Applet -r "$PLASMOID_ID" >/dev/null 2>&1 && \
        echo "Plasma-Widget entfernt (ggf. vorher vom Panel/Desktop entfernen)."
fi

echo ""
echo "Nicht gelöscht: $CONFDIR (Konfiguration und ggf. API-Key)."
echo "Wenn Sie auch das entfernen wollen:"
echo "  rm -r \"$CONFDIR\""
echo "  secret-tool clear service truenas-widget account api-key   # falls KWallet/secret-tool benutzt"
echo "Und den API-Key in TrueNAS widerrufen (siehe README)."
