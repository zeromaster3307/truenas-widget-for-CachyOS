#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Startet das Diagnose-Skript direkt aus dem Repo-Ordner (nur lesend).
#   ./diagnose.sh                       vollständige Diagnose
#   ./diagnose.sh --system <id>         nur ein System
#   ./diagnose.sh --nur-fingerabdruck --host <adresse> --port <port>
cd "$(dirname "$0")" || exit 1
exec python3 -m truenas_widget.diagnose "$@"
