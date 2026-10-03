#!/bin/sh
# Prüft alle Dateien, die ins Repo kommen (bereits eingecheckt + vorgemerkt),
# auf Dinge, die dort nichts zu suchen haben:
#   - IP-Adressen (ausser den Platzhaltern 192.168.1.20, 127.0.0.1, 0.0.0.0)
#   - E-Mail-Adressen (ausser noreply@anthropic.com aus der Commit-Signatur)
#   - SHA-256-Fingerabdrücke / lange Hex-Werte (ausser dem 00:00-Platzhalter)
#   - private Schlüssel
#   - Dateien mit verdächtigen Namen (api-key, config.toml)
# Aufruf vor jedem Commit:  ./tools/check-secrets.sh
cd "$(dirname "$0")/.." || exit 1
fail=0
files=$(git ls-files --cached --others --exclude-standard | grep -v '^tools/check-secrets.sh$')

for f in $files; do
    case "$f" in
        *api-key*|*api_key*|config.toml|*/config.toml)
            echo "VERDÄCHTIGE DATEI: $f"; fail=1 ;;
    esac
done

# shellcheck disable=SC2086
ips=$(grep -nIoE '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' $files 2>/dev/null \
      | grep -vE ':(192\.168\.1\.20|127\.0\.0\.1|0\.0\.0\.0)$')
[ -n "$ips" ] && { echo "IP-ADRESSEN gefunden:"; echo "$ips"; fail=1; }

# shellcheck disable=SC2086
mails=$(grep -nIoE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' $files 2>/dev/null \
      | grep -v 'noreply@anthropic.com')
[ -n "$mails" ] && { echo "E-MAIL-ADRESSEN gefunden:"; echo "$mails"; fail=1; }

# shellcheck disable=SC2086
hex=$(grep -nIoE '([0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2}|\b[0-9a-fA-F]{64}\b' $files 2>/dev/null \
      | grep -vE ':(00:){31}00$')
[ -n "$hex" ] && { echo "FINGERABDRUCK/HEX-WERT gefunden:"; echo "$hex"; fail=1; }

# shellcheck disable=SC2086
keys=$(grep -nIl 'BEGIN [A-Z ]*PRIVATE KEY' $files 2>/dev/null)
[ -n "$keys" ] && { echo "PRIVATER SCHLÜSSEL gefunden in:"; echo "$keys"; fail=1; }

if [ "$fail" -eq 0 ]; then
    echo "OK: keine Geheimnisse, echten Adressen oder Fingerabdrücke gefunden."
fi
exit $fail
