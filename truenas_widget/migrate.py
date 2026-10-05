"""Einmalige Umstellung von Version 0.3 (ein System) auf 0.4 (mehrere Systeme).

Wird von install.sh aufgerufen. Tut nur etwas, wenn config.toml noch den
alten Abschnitt [truenas] enthält:

  1. Legt systems/<id>.toml mit den bisherigen Werten an.
     Die Key-Datei bleibt, wo sie ist (z. B. ~/.config/truenas-widget/api-key);
     die neue Systemdatei verweist einfach darauf. Der Key wird nicht gelesen.
  2. Sichert die alte config.toml als config.toml.v0.3.bak und schreibt eine
     neue config.toml nur mit den allgemeinen Einstellungen.
  3. Versieht die Einträge in notified.json mit "<id>:", damit nach dem
     Update keine alten Meldungen erneut kommen.

Aufruf:  python3 -m truenas_widget.migrate
"""

from __future__ import annotations

import shutil
import sys
import tomllib
from pathlib import Path

from . import config as c
from . import notify, paths


def migrate(config_path: Path | None = None, systems_dir: Path | None = None,
            notified_path: Path | None = None) -> list[str]:
    config_path = Path(config_path) if config_path else paths.config_file()
    systems_dir = Path(systems_dir) if systems_dir else paths.systems_dir()
    notified_path = Path(notified_path) if notified_path else paths.notified_file()
    if not config_path.exists():
        return []
    with open(config_path, "rb") as fh:
        data = tomllib.load(fh)
    if "truenas" not in data:
        return []

    tn = dict(data.get("truenas") or {})
    key = dict(data.get("key") or {})
    chk = dict(data.get("checker") or {})
    notif = dict(data.get("notifications") or {})
    msgs = []

    taken = {p.stem for p in systems_dir.glob("*.toml")} if systems_dir.is_dir() else set()
    system_id = c.make_id(tn.get("name") or "truenas", taken)

    # Neue Systemdatei aus den alten Werten (Key-Pfad bleibt der alte)
    sysdata = {k: v for k, v in tn.items() if k in (
        "name", "host", "port", "username", "fingerprint_sha256", "api_version")}
    if "timeout_seconds" in chk:
        sysdata["timeout_seconds"] = chk["timeout_seconds"]
    source = key.get("source", "file")
    sysdata["key"] = {"source": source}
    if source == "file":
        sysdata["key"]["file"] = key.get("file", str(paths.legacy_key_file()))
    else:
        sysdata["key"]["secret_tool_attributes"] = key.get(
            "secret_tool_attributes", {"service": "truenas-widget", "account": "api-key"})
    cfg = c.system_from_dict(system_id, sysdata, require_fingerprint=False)
    c.write_private(systems_dir / f"{system_id}.toml", c.system_to_toml(cfg))
    msgs.append(f"System '{cfg.name}' übernommen nach {systems_dir / (system_id + '.toml')}")

    # Alte config.toml sichern, neue nur mit allgemeinen Einstellungen schreiben
    backup = config_path.with_name(config_path.name + ".v0.3.bak")
    shutil.copy2(config_path, backup)
    interval = chk.get("interval_minutes", 15)
    if interval not in c.ALLOWED_INTERVALS:
        interval = 15
    c.write_private(config_path, c.global_to_toml(interval, bool(notif.get("enabled", True))))
    msgs.append(f"Alte config.toml gesichert als {backup.name}")

    # Bereits Gemeldetes übernehmen
    if notified_path.exists():
        state = notify.load_state(notified_path)
        notify.save_state(notified_path, {k: {f"{system_id}:{x}" for x in v} for k, v in state.items()})
        msgs.append("Bereits gemeldete Ereignisse übernommen")
    return msgs


def main() -> int:
    try:
        for m in migrate():
            print(f"      {m}")
    except (c.ConfigError, OSError, tomllib.TOMLDecodeError) as exc:
        print(f"      FEHLER bei der Umstellung: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
