#!/bin/sh
# Startet das Diagnose-Skript direkt aus dem Repo-Ordner (nur lesend).
#   ./diagnose.sh                       vollständige Diagnose
#   ./diagnose.sh --nur-fingerabdruck   nur den Zertifikats-Fingerabdruck anzeigen
cd "$(dirname "$0")" || exit 1
exec python3 -m truenas_widget.diagnose "$@"
