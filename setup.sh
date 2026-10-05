#!/bin/sh
# Startet den Einrichtungs-Assistenten direkt aus dem Repo-Ordner.
#   ./setup.sh              mit Fenstern (kdialog)
#   ./setup.sh --terminal   im Terminal
cd "$(dirname "$0")" || exit 1
exec python3 -m truenas_widget.setup "$@"
