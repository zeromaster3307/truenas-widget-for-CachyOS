"""Einrichtungs-Assistent: TrueNAS-Systeme hinzufügen, ändern, entfernen.

Start:
  - Rechtsklick auf das Widget -> "TrueNAS hinzufügen/verwalten…"
  - Anwendungsmenü -> "TrueNAS-Widget einrichten"
  - Terminal: ./setup.sh   (im Repo)   oder   python3 -m truenas_widget.setup

Die Fenster kommen von "kdialog" (KDE). Fehlt kdialog oder gibt es keine
grafische Sitzung, fragt der Assistent im Terminal (Option --terminal erzwingt das).

Sicherheit:
  - Der API-Key wird in einem VERDECKTEN Feld abgefragt. kdialog gibt ihn über
    seine Ausgabe zurück, nie über die Befehlszeile. Der Assistent schreibt ihn
    direkt in die Key-Datei (Rechte 600) oder per Standardeingabe an secret-tool.
    Er wird nie angezeigt, nie protokolliert und nie an das Widget gegeben.
  - Der Zertifikats-Fingerabdruck wird angezeigt und muss von Ihnen bestätigt
    werden, BEVOR der Key gesendet wird.
  - Die Verbindungsprüfung benutzt denselben Code wie der Prüfer, also auch
    dieselbe Whitelist (nur lesende Methoden).
  - Der Assistent ändert nichts auf dem TrueNAS. Benutzer, Rolle und API-Key
    legen Sie dort selbst an; der Assistent öffnet nur die passenden Seiten.
"""

from __future__ import annotations

import argparse
import fcntl
import getpass
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, checker, keystore, paths
from . import config as c
from .wsclient import ConnectError, fetch_fingerprint

TITLE = "TrueNAS-Widget einrichten"
FP_TIMEOUT = 15.0


class Cancelled(Exception):
    """Der Benutzer hat abgebrochen."""


# ------------------------------------------------------------------------
# Oberflächen: kdialog (Fenster) und Terminal
# ------------------------------------------------------------------------

class KDialogUI:
    """Fenster über kdialog. Optionen belegt im Quellcode (KDE/kdialog,
    src/kdialog.cpp): --msgbox, --error, --inputbox, --password, --yesno
    (--yes-label/--no-label), --menu, --title. Exit-Code 0 = OK, 1 = Abbrechen;
    Eingaben kommen über die Standardausgabe zurück."""

    def __init__(self, exe: str = "kdialog"):
        self.exe = exe

    def _run(self, *args) -> tuple[int, str]:
        proc = subprocess.run([self.exe, "--title", TITLE, *args],
                              capture_output=True, text=True, check=False)
        return proc.returncode, proc.stdout

    def info(self, text: str) -> None:
        self._run("--msgbox", text)

    def error(self, text: str) -> None:
        self._run("--error", text)

    def ask(self, text: str, default: str = "") -> str:
        rc, outp = self._run("--inputbox", text, default)
        if rc != 0:
            raise Cancelled()
        return outp.rstrip("\n")

    def secret(self, text: str) -> str:
        # Der eingegebene Wert kommt NUR über stdout zurück (nicht in argv).
        rc, outp = self._run("--password", text)
        if rc != 0:
            raise Cancelled()
        return outp.rstrip("\n")

    def yesno(self, text: str, yes: str = "Ja", no: str = "Nein") -> bool:
        rc, _ = self._run("--yesno", text, "--yes-label", yes, "--no-label", no)
        return rc == 0

    def choose(self, text: str, options: list[tuple[str, str]]) -> str:
        args = ["--menu", text]
        for tag, label in options:
            args += [tag, label]
        rc, outp = self._run(*args)
        if rc != 0 or not outp.strip():
            raise Cancelled()
        return outp.strip()


class TerminalUI:
    """Dieselben Fragen im Terminal (Rückfall ohne kdialog)."""

    def info(self, text: str) -> None:
        print(f"\n{text}\n")

    def error(self, text: str) -> None:
        print(f"\nFEHLER: {text}\n")

    def _input(self, prompt: str) -> str:
        try:
            return input(prompt)
        except EOFError:
            raise Cancelled() from None

    def ask(self, text: str, default: str = "") -> str:
        hint = f" [{default}]" if default else ""
        value = self._input(f"{text}{hint}: ").strip()
        return value or default

    def secret(self, text: str) -> str:
        try:
            return getpass.getpass(f"{text} (Eingabe unsichtbar): ")
        except EOFError:
            raise Cancelled() from None

    def yesno(self, text: str, yes: str = "Ja", no: str = "Nein") -> bool:
        while True:
            answer = self._input(f"{text}\n  j = {yes} / n = {no}: ").strip().lower()
            if answer in ("j", "ja", "y", "yes"):
                return True
            if answer in ("n", "nein", "no"):
                return False

    def choose(self, text: str, options: list[tuple[str, str]]) -> str:
        print(f"\n{text}")
        for i, (_, label) in enumerate(options, 1):
            print(f"  {i}) {label}")
        print("  0) Beenden")
        while True:
            answer = self._input("Auswahl: ").strip()
            if answer == "0" or answer == "":
                raise Cancelled()
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1][0]


def pick_ui(force_terminal: bool = False):
    graphical = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if not force_terminal and graphical and shutil.which("kdialog"):
        return KDialogUI()
    if sys.stdin.isatty():
        return TerminalUI()
    return None


# ------------------------------------------------------------------------
# Hilfen zum Starten anderer Programme (ohne Key, ohne Shell)
# ------------------------------------------------------------------------

def open_url(url: str) -> None:
    """Öffnet eine https-Adresse im Browser."""
    if url.startswith("https://") and shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)


def start_check() -> None:
    """Stösst eine Prüfung an, damit das Widget die Änderung gleich zeigt."""
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "start", "--no-block", "truenas-widget.service"],
                       capture_output=True, check=False)


def secret_tool_available() -> bool:
    return shutil.which("secret-tool") is not None


def store_secret_tool(attributes: dict, label: str, secret: keystore.Secret) -> None:
    """Speichert den Key im Secret-Service. Der Key geht über stdin, nicht über argv."""
    args = ["secret-tool", "store", f"--label={label}"]
    for k, v in attributes.items():
        args += [k, v]
    proc = subprocess.run(args, input=secret.reveal(), text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise OSError("secret-tool konnte den Key nicht speichern (Wallet gesperrt?).")


def clear_secret_tool(attributes: dict) -> None:
    args = ["secret-tool", "clear"]
    for k, v in attributes.items():
        args += [k, v]
    subprocess.run(args, capture_output=True, check=False)


# ------------------------------------------------------------------------
# Der Assistent
# ------------------------------------------------------------------------

PREPARE_TEXT = (
    "Vorbereitung auf dem TrueNAS (einmalig, dort in der Weboberfläche):\n\n"
    "1. Credentials → Users → Add: eigenen Benutzer anlegen (z. B. widget-leser),\n"
    "   langes Zufallspasswort, SMB Access AUS, Shell Access AUS, SSH Access AUS.\n"
    "2. Beim Benutzer \"TrueNAS Access\" einschalten, Rolle \"Readonly Admin\" wählen.\n"
    "3. Benutzer-Menü oben rechts → \"My API Keys\" → Add: Key für diesen Benutzer\n"
    "   erzeugen. Er wird nur EINMAL angezeigt.\n\n"
    "Der Assistent öffnet die API-Keys-Seite gleich im Browser.\n"
    "Den Key geben Sie danach in einem verdeckten Feld ein."
)


class Assistant:
    def __init__(self, ui, *, fetch_fp=fetch_fingerprint, check=checker.check_system,
                 opener=open_url, starter=start_check, has_secret_tool=secret_tool_available,
                 store_secret=store_secret_tool, clear_secret=clear_secret_tool):
        self.ui = ui
        self.fetch_fp = fetch_fp
        self.check = check
        self.opener = opener
        self.starter = starter
        self.has_secret_tool = has_secret_tool
        self.store_secret = store_secret
        self.clear_secret = clear_secret

    # ---------- Hauptmenü ----------
    def run(self) -> int:
        try:
            app = c.load(require_fingerprint=False)
        except c.ConfigError as exc:
            self.ui.error(str(exc))
            return 2
        try:
            if not app.systems and not app.broken:
                self.add()
                return 0
            while True:
                app = c.load(require_fingerprint=False)
                choice = self.ui.choose("Was möchten Sie tun?", self._menu(app))
                self._dispatch(choice, app)
        except Cancelled:
            return 0

    def _menu(self, app) -> list[tuple[str, str]]:
        options = [("add", "Neues TrueNAS hinzufügen")]
        for s in app.systems:
            options += [
                (f"edit:{s.id}", f"{s.name}: Name/Adresse/Benutzer ändern"),
                (f"key:{s.id}", f"{s.name}: API-Key erneuern"),
                (f"fp:{s.id}", f"{s.name}: Zertifikats-Fingerabdruck neu prüfen"),
                (f"del:{s.id}", f"{s.name}: entfernen"),
            ]
        for bad_id, _ in app.broken:
            options.append((f"delbad:{bad_id}", f"{bad_id} (fehlerhafte Datei): entfernen"))
        return options

    def _dispatch(self, choice: str, app) -> None:
        action, _, sid = choice.partition(":")
        if action == "add":
            return self.add()
        if action == "delbad":
            return self.remove_broken(sid)
        system = next((s for s in app.systems if s.id == sid), None)
        if system is None:
            return
        {"edit": self.edit, "key": self.renew_key, "fp": self.recheck_fingerprint,
         "del": self.remove}[action](system)

    # ---------- Bausteine ----------
    def _ask_host(self, default: str = "") -> str:
        while True:
            raw = self.ui.ask("Adresse des TrueNAS (IP oder Name, ohne http://),\n"
                              "z. B. 192.168.1.20 oder nas.tailnet.ts.net", default)
            try:
                return c.parse_host(raw)
            except c.ConfigError as exc:
                self.ui.error(str(exc))
                default = raw

    def _ask_port(self, default: int = 443) -> int:
        while True:
            raw = self.ui.ask("HTTPS-Port der TrueNAS-Weboberfläche", str(default)).strip()
            if raw.isdigit() and 1 <= int(raw) <= 65535:
                return int(raw)
            self.ui.error("Bitte eine Zahl zwischen 1 und 65535 eingeben (meist 443).")

    def _confirm_fingerprint(self, host: str, port: int, old: str | None = None) -> str:
        """Holt den Fingerabdruck und lässt ihn bestätigen. Wirft Cancelled bei Nein."""
        while True:
            try:
                fp = self.fetch_fp(host, port, FP_TIMEOUT)
                break
            except ConnectError as exc:
                if not self.ui.yesno(f"TrueNAS nicht erreichbar:\n{exc}\n\nNochmal versuchen?",
                                     "Nochmal", "Abbrechen"):
                    raise Cancelled() from None
        if old and fp == old:
            self.ui.info("Der Fingerabdruck ist unverändert:\n\n" + c.format_fingerprint(fp))
            return fp
        intro = ("Der Fingerabdruck hat sich GEÄNDERT.\n\nBisher:\n" + c.format_fingerprint(old)
                 + "\n\nJetzt:\n") if old else "SHA-256-Fingerabdruck des TrueNAS-Zertifikats:\n\n"
        text = (intro + c.format_fingerprint(fp) + "\n\n"
                "Bitte vergleichen: TrueNAS-Seite im Browser öffnen → Schloss-/Warnsymbol neben "
                "der Adresse → Zertifikat anzeigen → SHA-256-Fingerabdruck.\n\n"
                "Soll ich die Seite jetzt im Browser öffnen?")
        if self.ui.yesno(text, "Im Browser öffnen", "Nicht nötig"):
            self.opener(f"https://{host}:{port}/")
        if not self.ui.yesno("Stimmt der Fingerabdruck EXAKT mit dem im Browser überein?\n\n"
                             + c.format_fingerprint(fp) + "\n\n"
                             "Nur bestätigen, wenn Sie sicher sind. Sonst könnte sich ein "
                             "anderes Gerät als TrueNAS ausgeben.",
                             "Ja, stimmt überein", "Nein, abbrechen"):
            raise Cancelled()
        return fp

    def _ask_key(self, cfg) -> keystore.Secret:
        while True:
            value = self.ui.secret(f"API-Key für \"{cfg.name}\" einfügen\n"
                                   "(wird nicht angezeigt; nie in Chats/Screenshots weitergeben)")
            value = value.strip()
            if value and " " not in value and "\n" not in value:
                return keystore.Secret(value)
            self.ui.error("Das sieht nicht nach einem API-Key aus (leer oder mit Leerzeichen).")

    def _test(self, cfg, secret) -> bool:
        """Testet die Verbindung. True = speichern, False = Key nochmal eingeben."""
        entry = self.check(cfg, secret)
        kind = entry.get("offline_kind")
        reason = secret.redact(entry.get("offline_reason") or "")
        if entry["status"] != "offline":
            self.ui.info(f"Verbindung klappt.\nAktueller Status von \"{cfg.name}\": "
                         f"{entry['status_text']}.")
            return True
        if kind == "auth":
            if self.ui.yesno(f"Anmeldung fehlgeschlagen:\n{reason}\n\n"
                             "Benutzername und Key prüfen. Key nochmal eingeben?",
                             "Key nochmal eingeben", "Abbrechen"):
                return False
            raise Cancelled()
        if kind == "incomplete":
            text = (f"Die Anmeldung klappt, aber nicht alle Abfragen sind erlaubt:\n"
                    + "\n".join(entry.get("problems", []))
                    + "\n\nHat der Benutzer die Rolle \"Readonly Admin\"?\n\nTrotzdem speichern?")
        else:
            text = f"Test nicht erfolgreich:\n{reason}\n\nTrotzdem speichern?"
        if self.ui.yesno(text, "Trotzdem speichern", "Abbrechen"):
            return True
        raise Cancelled()

    def _ask_storage(self) -> str:
        if not self.has_secret_tool():
            return "file"
        return self.ui.choose(
            "Wo soll der API-Key gespeichert werden?",
            [("file", "Datei nur für Sie lesbar (Standard, Rechte 600)"),
             ("secret-tool", "KDE-Passwortspeicher / KWallet (secret-tool)")])

    def _save_key(self, cfg, secret) -> None:
        if cfg.key_source == "secret-tool":
            self.store_secret(cfg.secret_tool_attributes, f"TrueNAS-Widget: {cfg.name}", secret)
        else:
            c.write_private(cfg.key_file, secret.reveal() + "\n")

    def _save_system(self, cfg) -> Path:
        path = paths.systems_dir() / f"{cfg.id}.toml"
        c.write_private(path, c.system_to_toml(cfg))
        return path

    def _key_loop(self, cfg) -> keystore.Secret:
        while True:
            secret = self._ask_key(cfg)
            if self._test(cfg, secret):
                return secret

    # ---------- Aktionen ----------
    def add(self) -> None:
        app = c.load(require_fingerprint=False)
        taken = {s.id for s in app.systems} | {b[0] for b in app.broken}
        self.ui.info(PREPARE_TEXT)
        name = ""
        while not name:
            name = self.ui.ask("Anzeigename für dieses TrueNAS (z. B. homelab, remote)",
                               "homelab" if not app.systems else "").strip()
        system_id = c.make_id(name, taken)
        host = self._ask_host()
        port = self._ask_port()
        fp = self._confirm_fingerprint(host, port)
        username = self.ui.ask("Benutzername auf dem TrueNAS (dem der API-Key gehört)",
                               "widget-leser").strip()
        if self.ui.yesno("API-Keys-Seite dieses TrueNAS jetzt im Browser öffnen?\n"
                         "(Benutzer-Menü oben rechts → My API Keys)",
                         "Öffnen", "Nicht nötig"):
            self.opener(f"https://{host}:{port}/credentials/users/api-keys")
        source = self._ask_storage()
        cfg = c.SystemConfig(id=system_id, name=name, host=host, port=port,
                             username=username, fingerprint=fp, key_source=source)
        secret = self._key_loop(cfg)
        self._save_key(cfg, secret)
        self._save_system(cfg)
        self.starter()
        self.ui.info(f"Fertig: \"{name}\" wurde hinzugefügt.\n"
                     "Das Widget zeigt es nach der nächsten Prüfung (in wenigen Sekunden).")

    def edit(self, cfg) -> None:
        name = self.ui.ask("Anzeigename", cfg.name).strip() or cfg.name
        host = self._ask_host(cfg.host)
        port = self._ask_port(cfg.port)
        username = self.ui.ask("Benutzername auf dem TrueNAS", cfg.username).strip() or cfg.username
        fp = cfg.fingerprint
        if (host, port) != (cfg.host, cfg.port):
            fp = self._confirm_fingerprint(host, port)
        new = c.SystemConfig(id=cfg.id, name=name, host=host, port=port, username=username,
                             fingerprint=fp, api_version=cfg.api_version, key_source=cfg.key_source,
                             key_file=cfg.key_file, secret_tool_attributes=cfg.secret_tool_attributes,
                             timeout_seconds=cfg.timeout_seconds)
        if (host, port, username) != (cfg.host, cfg.port, cfg.username):
            # Verbindung mit dem gespeicherten Key testen
            try:
                secret = keystore.load_key(new)
                if not self._test(new, secret):
                    secret = self._key_loop(new)
                    self._save_key(new, secret)
            except keystore.KeyError_ as exc:
                self.ui.error(f"Gespeicherter Key nicht lesbar: {exc}\nBitte neu eingeben.")
                self._save_key(new, self._key_loop(new))
        self._save_system(new)
        self.starter()
        self.ui.info(f"Gespeichert: \"{name}\".")

    def renew_key(self, cfg) -> None:
        secret = self._key_loop(cfg)
        self._save_key(cfg, secret)
        self.starter()
        self.ui.info(f"Neuer API-Key für \"{cfg.name}\" gespeichert.\n"
                     "Den alten Key bitte auf dem TrueNAS unter \"My API Keys\" löschen.")

    def recheck_fingerprint(self, cfg) -> None:
        fp = self._confirm_fingerprint(cfg.host, cfg.port, old=cfg.fingerprint)
        if fp != cfg.fingerprint:
            cfg.fingerprint = fp
            self._save_system(cfg)
            self.starter()
            self.ui.info(f"Neuer Fingerabdruck für \"{cfg.name}\" gespeichert.")

    def remove(self, cfg) -> None:
        if not self.ui.yesno(f"\"{cfg.name}\" wirklich aus dem Widget entfernen?",
                             "Entfernen", "Abbrechen"):
            return
        (paths.systems_dir() / f"{cfg.id}.toml").unlink(missing_ok=True)
        if cfg.key_source == "secret-tool":
            self.clear_secret(cfg.secret_tool_attributes)
        elif cfg.key_file and Path(cfg.key_file).exists():
            if self.ui.yesno(f"Gespeicherten API-Key auch löschen?\n({cfg.key_file})",
                             "Key löschen", "Behalten"):
                Path(cfg.key_file).unlink()
        self.starter()
        self.ui.info(f"\"{cfg.name}\" wurde entfernt.\n"
                     "Den API-Key bitte auch auf dem TrueNAS unter \"My API Keys\" löschen.")

    def remove_broken(self, system_id: str) -> None:
        if self.ui.yesno(f"Fehlerhafte Datei systems/{system_id}.toml entfernen?",
                         "Entfernen", "Abbrechen"):
            (paths.systems_dir() / f"{system_id}.toml").unlink(missing_ok=True)
            self.starter()


def _single_instance():
    """Verhindert, dass der Assistent doppelt läuft (z. B. zweimal geklickt)."""
    paths.cache_dir().mkdir(parents=True, exist_ok=True)
    fh = open(paths.cache_dir() / "setup.lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return fh


def main(argv=None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    p = argparse.ArgumentParser(description=f"{TITLE} (Version {__version__})")
    p.add_argument("--terminal", action="store_true", help="im Terminal fragen statt mit Fenstern")
    args = p.parse_args(argv)
    ui = pick_ui(args.terminal)
    if ui is None:
        print("Kein kdialog/keine grafische Sitzung und kein Terminal. "
              "Bitte im Terminal starten: python3 -m truenas_widget.setup --terminal", file=sys.stderr)
        return 2
    lock = _single_instance()
    if lock is None:
        return 0  # läuft schon
    try:
        return Assistant(ui).run()
    finally:
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
