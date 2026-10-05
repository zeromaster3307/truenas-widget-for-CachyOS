"""TrueNAS-Status-Widget: nur lesender Prüfer für ein TrueNAS-System.

Dieses Paket enthält:
- config.py     : liest und prüft die Konfiguration (config.toml + systems/*.toml)
- keystore.py   : liest den API-Key (Datei oder secret-tool), ohne ihn je auszugeben
- wsclient.py   : kleiner WebSocket-Client über TLS mit Fingerabdruck-Prüfung
- rpc.py        : JSON-RPC-Client mit fester Whitelist (nur lesende Methoden)
- checker.py    : der eigentliche Prüfer (wird vom systemd-Timer gestartet)
- notify.py     : Desktop-Benachrichtigungen (notify-send), jedes Ereignis nur einmal
- diagnose.py   : Diagnose-Skript für den Abgleich mit dem echten System
- setup.py      : Einrichtungs-Assistent (TrueNAS hinzufügen/ändern/entfernen)
- migrate.py    : einmalige Umstellung der Konfiguration von Version 0.3
"""

__version__ = "0.4.1"
