"""Tests: Anzeige-Logik des Plasma-Widgets (logic.js), ausgeführt mit Node.js.

Die QML-Oberfläche selbst kann ohne Plasma nicht getestet werden; die
Entscheidungen (Farbe, veraltet, welche Zeilen, max. 5 Alerts) stecken aber
alle in logic.js und werden hier geprüft. Ohne Node.js wird übersprungen.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

LOGIC = Path(__file__).parent.parent / "plasmoid" / "package" / "contents" / "code" / "logic.js"

RUNNER = r"""
const fs = require('fs'); const vm = require('vm');
const src = fs.readFileSync(process.argv[1], 'utf8').replace(/^\.pragma library\s*$/m, '');
const ctx = {}; vm.createContext(ctx); vm.runInContext(src, ctx);
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const out = input.map(c => {
  const st = c.text === undefined ? c.st : ctx.parseStatus(c.text);
  const eff = ctx.effective(st, c.now, c.ignored || {});
  const blocks = ctx.blocks(st, eff);
  return { eff, color: ctx.color(eff.status), blocks, global: ctx.globalLines(st, eff),
           lines: blocks.length ? blocks[0].lines : [],
           tooltip: ctx.tooltip(st, eff), last: ctx.lastCheckText(st, c.now),
           menu: ctx.menuSystems(st) };
});
process.stdout.write(JSON.stringify(out));
"""

NOW = 1_700_000_000
TOP_LEVEL = ("checked_at_epoch", "interval_minutes")


def system(status="ok", sid="homelab", **kw):
    base = {"id": sid, "name": sid, "web_url": f"https://{sid}.invalid/", "status": status,
            "offline_kind": None, "offline_reason": None, "offline_count": 0,
            "app_updates": [], "system_update": {"available": False, "new_version": None},
            "alerts": [], "problems": [], "incomplete": []}
    base.update(kw)
    return base


def st(status="ok", **kw):
    """status.json (Format 2) mit EINEM System; checked_at_epoch/interval_minutes oben."""
    top = {k: kw.pop(k) for k in TOP_LEVEL if k in kw}
    data = {"schema": 2, "checked_at_epoch": NOW, "status": status, "systems": [system(status, **kw)]}
    data.update(top)
    return data


def multi(*systems, **top):
    data = {"schema": 2, "checked_at_epoch": NOW, "systems": list(systems)}
    data.update(top)
    return data


@unittest.skipUnless(shutil.which("node"), "Node.js nicht installiert")
class WidgetLogicTests(unittest.TestCase):
    def run_cases(self, cases):
        proc = subprocess.run(["node", "-e", RUNNER, str(LOGIC)], input=json.dumps(cases),
                              capture_output=True, text=True, check=True)
        return json.loads(proc.stdout)

    def one(self, status_obj, now_offset_min=1, text=None, ignored=None):
        case = {"now": (NOW + now_offset_min * 60) * 1000, "ignored": ignored or {}}
        if text is not None:
            case["text"] = text
        else:
            case["st"] = status_obj
        return self.run_cases([case])[0]

    def test_ok_zeigt_nur_name_und_ok(self):
        r = self.one(st("ok"))
        self.assertEqual(r["eff"]["text"], "OK")
        self.assertEqual(r["color"], "#2e9d4f")
        self.assertEqual(r["lines"], [])
        self.assertEqual(r["blocks"][0]["name"], "homelab")
        self.assertEqual(r["blocks"][0]["url"], "https://homelab.invalid/")

    def test_farben(self):
        for status, color in (("updates", "#d4a800"), ("warning", "#ef7d00"),
                              ("critical", "#d62828"), ("offline", "#8a8a8a")):
            self.assertEqual(self.one(st(status))["color"], color, status)

    def test_veraltet_nach_45_minuten(self):
        self.assertEqual(self.one(st("ok"), now_offset_min=44)["eff"]["status"], "ok")
        r = self.one(st("ok"), now_offset_min=46)
        self.assertEqual(r["eff"]["status"], "offline")
        self.assertEqual(r["eff"]["text"], "Veraltet")
        self.assertEqual(r["blocks"], [])
        self.assertIn("älter als 45", r["global"][0]["text"])

    def test_keine_datei(self):
        r = self.one(None, text="")
        self.assertEqual(r["eff"]["status"], "offline")
        self.assertIn("Noch keine Daten", r["eff"]["reason"])

    def test_kaputte_oder_alte_datei(self):
        self.assertEqual(self.one(None, text="{kaputt")["eff"]["status"], "offline")
        old = json.dumps({"schema": 1, "status": "ok", "checked_at_epoch": NOW})  # älteres Format
        self.assertIn("Noch keine Daten", self.one(None, text=old)["eff"]["reason"])

    def test_nichts_eingerichtet(self):
        r = self.one(multi())
        self.assertEqual(r["eff"]["text"], "Nicht eingerichtet")
        self.assertIn("TrueNAS hinzufügen", r["global"][0]["text"])

    def test_updates_zeilen(self):
        r = self.one(st("updates",
                        app_updates=[{"name": "jellyfin", "current": "1.2.3", "new": "1.2.4"},
                                     {"name": "eigen", "current": "1.0", "new": None}],
                        system_update={"available": True, "new_version": "25.10.8"}))
        texts = [l["text"] for l in r["lines"]]
        self.assertIn("jellyfin: 1.2.3 → 1.2.4", texts)
        self.assertIn("eigen: 1.0 → neues Image", texts)
        self.assertIn("Neue Version: 25.10.8", texts)
        self.assertEqual(r["tooltip"], "2 App-Updates, Systemupdate 25.10.8")

    def test_maximal_fuenf_alerts(self):
        alerts = [{"id": str(i), "level": "WARNING", "severity": "warning", "text": f"A{i}"}
                  for i in range(8)]
        r = self.one(st("warning", alerts=alerts))
        alert_lines = [l for l in r["lines"] if l["kind"] == "alert"]
        self.assertEqual(len(alert_lines), 5)
        self.assertEqual(r["lines"][-1]["text"], "+ 3 weitere")

    def test_offline_grund_wird_gezeigt(self):
        r = self.one(st("offline", offline_reason="Zertifikats-Fingerabdruck stimmt nicht überein."))
        self.assertEqual(r["eff"]["text"], "Offline")
        self.assertIn("Fingerabdruck", r["lines"][0]["text"])
        self.assertIn("Fingerabdruck", r["tooltip"])

    def test_letzte_pruefung(self):
        self.assertTrue(self.one(st("ok"))["last"].startswith("Letzte Prüfung: "))


@unittest.skipUnless(shutil.which("node"), "Node.js nicht installiert")
class MultiSystemLogicTests(WidgetLogicTests):
    """Mehrere Systeme: Panel-Farbe, "offline ignorieren", Blöcke, Tooltip, Menü."""

    def remote_offline(self, count, kind="unreachable"):
        return system("offline", "remote", offline_kind=kind, offline_count=count,
                      offline_reason="Nicht erreichbar: Zeitüberschreitung")

    def test_einmal_offline_noch_gruen(self):
        r = self.one(multi(system("ok"), self.remote_offline(1)))
        self.assertEqual(r["eff"]["status"], "ok")

    def test_zweimal_offline_orange(self):
        r = self.one(multi(system("ok"), self.remote_offline(2)))
        self.assertEqual(r["eff"]["status"], "warning")
        self.assertEqual(r["color"], "#ef7d00")

    def test_offline_ignorieren(self):
        r = self.one(multi(system("ok"), self.remote_offline(5)), ignored={"remote": True})
        self.assertEqual(r["eff"]["status"], "ok")
        # Zeile von remote zeigt trotzdem weiter "nicht erreichbar"
        remote = r["blocks"][1]
        self.assertEqual(remote["text"], "Offline")
        self.assertIn("Nicht erreichbar", remote["lines"][0]["text"])

    def test_fingerabdruck_nie_ignorierbar(self):
        r = self.one(multi(system("ok"), self.remote_offline(1, "fingerprint")), ignored={"remote": True})
        self.assertEqual(r["eff"]["status"], "warning")

    def test_alle_offline_grau(self):
        r = self.one(multi(system("offline", "homelab", offline_kind="unreachable", offline_count=3),
                           self.remote_offline(3)))
        self.assertEqual(r["eff"]["status"], "offline")
        self.assertIn("Kein TrueNAS erreichbar", r["eff"]["reason"])

    def test_kritisch_gewinnt(self):
        r = self.one(multi(system("critical"), self.remote_offline(9)))
        self.assertEqual(r["eff"]["status"], "critical")

    def test_bloecke_und_drei_alerts_pro_system(self):
        alerts = [{"id": str(i), "level": "WARNING", "severity": "warning", "text": f"A{i}"}
                  for i in range(5)]
        r = self.one(multi(system("warning", alerts=alerts), system("ok", "remote")))
        self.assertEqual([b["name"] for b in r["blocks"]], ["homelab", "remote"])
        lines = r["blocks"][0]["lines"]
        self.assertEqual(len([l for l in lines if l["kind"] == "alert"]), 3)
        self.assertEqual(lines[-1]["text"], "+ 2 weitere")
        self.assertEqual(r["blocks"][1]["lines"], [])

    def test_tooltip_pro_system(self):
        r = self.one(multi(system("ok"), self.remote_offline(2)))
        self.assertEqual(r["tooltip"], "homelab: OK\nremote: nicht erreichbar")

    def test_menue_nur_bei_mehreren_systemen(self):
        self.assertEqual(self.one(st("ok"))["menu"], [])
        r = self.one(multi(system("ok"), system("ok", "remote")))
        self.assertEqual(r["menu"], [{"id": "homelab", "name": "homelab"}, {"id": "remote", "name": "remote"}])

    def test_haekchen_liste(self):
        r = js('toggledList(["a", "b"], "b", false)', 'toggledList(["a"], "b", true)',
               'toggledList(["a"], "a", true)', 'ignoredMap(["x"])')
        self.assertEqual(r, [["a"], ["a", "b"], ["a"], {"x": True}])

    def test_hinweis_auch_bei_ok(self):
        r = self.one(multi(system("ok", notices=["Zertifikat läuft in 10 Tagen ab (11.10.2026)."]),
                           system("ok", "remote")))
        self.assertEqual(r["eff"]["status"], "ok")               # Farbe bleibt grün
        line = r["blocks"][0]["lines"][0]
        self.assertEqual((line["kind"], line["level"]), ("notice", "warning"))
        self.assertIn("homelab: Zertifikat läuft in 10 Tagen ab", r["tooltip"])

    def test_fremde_url_wird_nicht_geoeffnet(self):
        r = self.one(multi(system("ok", web_url="http://homelab.invalid/"), system("ok", "remote")))
        self.assertEqual(r["blocks"][0]["url"], "")


JS_EVAL = r"""
const fs = require('fs'); const vm = require('vm');
const src = fs.readFileSync(process.argv[1], 'utf8').replace(/^\.pragma library\s*$/m, '');
const ctx = {}; vm.createContext(ctx); vm.runInContext(src, ctx);
const exprs = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(exprs.map(e => vm.runInContext(e, ctx))));
"""


def js(*exprs):
    proc = subprocess.run(["node", "-e", JS_EVAL, str(LOGIC)], input=json.dumps(list(exprs)),
                          capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


@unittest.skipUnless(shutil.which("node"), "Node.js nicht installiert")
class StartupCheckLogicTests(unittest.TestCase):
    """Beim Start: nur prüfen, wenn status.json fehlt oder älter als das Intervall ist."""

    def needs(self, st_obj, age_min):
        now = (NOW + age_min * 60) * 1000
        return js(f"needsCheck({json.dumps(st_obj)}, {now})")[0]

    def test_datei_fehlt(self):
        self.assertTrue(self.needs(None, 0))

    def test_frisch_tut_nichts(self):
        self.assertFalse(self.needs(st("ok", interval_minutes=15), 14))

    def test_aelter_als_intervall(self):
        self.assertTrue(self.needs(st("ok", interval_minutes=15), 16))

    def test_konfiguriertes_intervall_zaehlt(self):
        self.assertFalse(self.needs(st("ok", interval_minutes=60), 50))
        self.assertTrue(self.needs(st("ok", interval_minutes=5), 6))

    def test_ohne_intervall_gilt_standard_15(self):
        self.assertFalse(self.needs(st("ok"), 14))
        self.assertTrue(self.needs(st("ok"), 16))

    def test_auch_offline_status_ist_frisch(self):
        # "offline" ist ein gültiges Ergebnis - kein Grund für eine weitere Prüfung
        self.assertFalse(self.needs(st("offline", interval_minutes=15), 1))

    def test_ergebnis_der_pruefung(self):
        new = json.dumps(st("ok", checked_at_epoch=NOW + 100))
        old = json.dumps(st("ok"))
        r = js(f"checkOutcome({NOW}, {new}, null, false)",   # neue Daten -> fertig
               f"checkOutcome({NOW}, {old}, null, false)",   # noch nichts -> warten
               f"checkOutcome({NOW}, {old}, 0, false)",      # Dienst fertig, Datei noch alt -> warten
               f"checkOutcome({NOW}, {old}, 75, false)",     # anderes Widget prüft gerade -> warten
               f"checkOutcome({NOW}, {old}, null, true)",    # Timeout -> fehlgeschlagen
               f"checkOutcome({NOW}, {old}, 5, false)",      # systemctl-Fehler -> fehlgeschlagen
               f"checkOutcome(0, null, null, true)",         # keine Datei + Timeout
               f"checkOutcome(0, {new}, 0, false)",          # erste Datei überhaupt
               f"checkOutcome({NOW}, {old}, 76, false)",     # zu kurz nach letztem Start -> übersprungen
               f"checkOutcome({NOW}, {new}, 76, false)")     # ... aber neue Daten sind da -> fertig
        self.assertEqual(r, ["done", "pending", "pending", "pending", "failed", "failed", "failed", "done",
                             "skipped", "done"])

    def test_anzeige_pruefe_und_fehlgeschlagen(self):
        r = js('headerText({text: "OK"}, true)', 'headerText({text: "OK"}, false)',
               'linesWithCheckState([], true, "")', 'linesWithCheckState([], false, "")',
               'linesWithCheckState([{kind: "app", text: "x", level: ""}], false, TOO_SOON_TEXT)')
        self.assertEqual(r[0], "Prüfe…")
        self.assertEqual(r[1], "OK")
        self.assertIn("Prüfung fehlgeschlagen", r[2][0]["text"])
        self.assertEqual(r[3], [])
        self.assertIn("Bitte kurz warten", r[4][0]["text"])  # Hinweis oben ...
        self.assertEqual(r[4][1]["text"], "x")               # ... bisheriger Inhalt bleibt

    def test_konstanten(self):
        self.assertEqual(js("MIN_TRIGGER_SECONDS", "SKIPPED_EXIT_CODE", "TOO_SOON_EXIT_CODE"), [60, 75, 76])


@unittest.skipUnless(shutil.which("node") and shutil.which("flock"), "Node.js oder flock fehlt")
class TriggerCommandTests(unittest.TestCase):
    """Führt den echten Startbefehl des Widgets mit sh aus (systemctl ist eine Attrappe)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.bin = root / "bin"
        self.calls = root / "calls"
        self.home.mkdir()
        self.bin.mkdir()
        self.sleep = 0
        self.command = js("triggerCommand()")[0]

    def tearDown(self):
        self.tmp.cleanup()

    def fake_systemctl(self, sleep=0):
        f = self.bin / "systemctl"
        f.write_text(f'#!/bin/sh\necho "$*" >> "{self.calls}"\nsleep {sleep}\n')
        f.chmod(0o755)

    def env(self):
        return {"HOME": str(self.home), "PATH": f"{self.bin}:{os.environ['PATH']}"}

    def run_trigger(self):
        return subprocess.run(["sh", "-c", self.command], env=self.env(), capture_output=True, text=True)

    def calls_list(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_startet_nur_den_einen_dienst_ohne_parameter(self):
        self.fake_systemctl()
        self.assertEqual(self.run_trigger().returncode, 0)
        self.assertEqual(self.calls_list(), ["--user start truenas-widget.service"])
        # Befehl enthält keine Adresse, keinen Key, keinen Netzzugriff
        for forbidden in ("wss://", "https://", "http://", "api-key", "secret-tool", "curl", "python"):
            self.assertNotIn(forbidden, self.command)

    def test_hoechstens_einmal_pro_minute(self):
        self.fake_systemctl()
        self.assertEqual(self.run_trigger().returncode, 0)
        second = self.run_trigger()
        self.assertEqual(second.returncode, 76)          # zu kurz her -> übersprungen
        self.assertEqual(len(self.calls_list()), 1)
        # Zeitstempel künstlich 61 s zurückdatieren -> wieder erlaubt
        stamp = self.home / ".cache" / "truenas-widget" / "widget-trigger.stamp"
        stamp.write_text(str(int(stamp.read_text()) - 61))
        self.assertEqual(self.run_trigger().returncode, 0)
        self.assertEqual(len(self.calls_list()), 2)

    def test_zwei_widgets_gleichzeitig_nur_ein_start(self):
        self.fake_systemctl(sleep=2)  # Prüfung dauert, Sperre bleibt so lange gehalten
        first = subprocess.Popen(["sh", "-c", self.command], env=self.env())
        import time
        for _ in range(50):  # warten, bis der erste Start läuft
            if self.calls_list():
                break
            time.sleep(0.05)
        stamp = self.home / ".cache" / "truenas-widget" / "widget-trigger.stamp"
        stamp.write_text("0")  # selbst ohne Zeitsperre greift die Datei-Sperre (flock)
        self.assertEqual(self.run_trigger().returncode, 75)
        self.assertEqual(first.wait(timeout=10), 0)
        self.assertEqual(len(self.calls_list()), 1)

    def test_kaputter_zeitstempel(self):
        self.fake_systemctl()
        d = self.home / ".cache" / "truenas-widget"
        d.mkdir(parents=True)
        (d / "widget-trigger.stamp").write_text("Unsinn")
        self.assertEqual(self.run_trigger().returncode, 0)

    def test_zeitstempel_in_der_zukunft_blockiert_nicht_dauerhaft(self):
        self.fake_systemctl()
        d = self.home / ".cache" / "truenas-widget"
        d.mkdir(parents=True)
        (d / "widget-trigger.stamp").write_text("99999999999")  # Uhr wurde zurückgestellt
        self.assertEqual(self.run_trigger().returncode, 0)

    def test_systemctl_fehler_wird_weitergegeben(self):
        f = self.bin / "systemctl"
        f.write_text("#!/bin/sh\nexit 5\n")  # z. B. Dienst nicht installiert
        f.chmod(0o755)
        self.assertEqual(self.run_trigger().returncode, 5)


class ManualCheckQmlTests(unittest.TestCase):
    """"Jetzt prüfen" im Rechtsklick-Menü (statische Prüfung der QML-Datei)."""

    QML = (Path(__file__).parent.parent / "plasmoid" / "package" / "contents" / "ui" / "main.qml").read_text()

    def test_menueeintrag_vorhanden(self):
        self.assertIn("Plasmoid.contextualActions", self.QML)
        self.assertIn('text: "Jetzt prüfen"', self.QML)
        self.assertIn("onTriggered: root.startCheck(true)", self.QML)

    def test_widget_startet_nur_die_zwei_befehle(self):
        # Ausser in Kommentaren darf "systemctl" nirgends direkt im QML stehen;
        # gestartet werden ausschliesslich Logic.triggerCommand() und
        # Logic.setupCommand(); ansonsten wird nur status.json gelesen.
        code = "\n".join(l for l in self.QML.splitlines() if not l.strip().startswith("//"))
        self.assertNotIn("systemctl", code)
        self.assertNotIn("setup.sh", code)
        sources = re.findall(r"(\w+)\.connectSource\(([^)]*\)?)\)", code)
        self.assertEqual(sorted(sources), sorted([
            ("trigger", "Logic.triggerCommand()"),
            ("setup", "Logic.setupCommand()"),
            ("reader", "readCommand"),
        ]))
        self.assertIn("'cat \"${XDG_CACHE_HOME:-$HOME/.cache}/truenas-widget/status.json\" 2>/dev/null'", code)

    def test_menueeintraege(self):
        self.assertIn('text: "TrueNAS hinzufügen/verwalten…"', self.QML)
        self.assertIn('text: modelData.name + ": offline ignorieren"', self.QML)
        self.assertIn("checkable: true", self.QML)


class DesktopViewQmlTests(unittest.TestCase):
    """Desktop: immer Detailansicht; Panel: Icon, Details per Klick; Knopf in beiden."""

    QML = ManualCheckQmlTests.QML

    def test_desktop_immer_detailansicht(self):
        self.assertIn("preferredRepresentation: inPanel ? compactRepresentation : fullRepresentation", self.QML)
        self.assertIn("switchWidth: inPanel ? Number.POSITIVE_INFINITY : 0", self.QML)
        self.assertIn("switchHeight: inPanel ? Number.POSITIVE_INFINITY : 0", self.QML)

    def test_aktualisieren_knopf_ueberall_in_der_detailansicht(self):
        idx = self.QML.rindex("PlasmaComponents3.ToolButton {", 0, self.QML.index('icon.name: "view-refresh"', self.QML.index("Gemeinsame Fussnote")))
        button = self.QML[idx:self.QML.index("}", idx)]
        self.assertNotIn("visible:", button)  # Desktop UND Panel-Popup
        self.assertIn("text: \"Jetzt prüfen\"", self.QML)  # Rechtsklick-Menü bleibt
        self.assertIn("onClicked: root.startCheck(true)", button)  # gleiche Prüfung wie "Jetzt prüfen"
        self.assertIn("enabled: !root.checking", button)


@unittest.skipUnless(shutil.which("node"), "Node.js nicht installiert")
class SetupCommandTests(unittest.TestCase):
    """Der Assistent wird nur über den Starter von install.sh aufgerufen."""

    def test_befehl(self):
        cmd = js("setupCommand()")[0]
        self.assertEqual(cmd, 'exec "${XDG_DATA_HOME:-$HOME/.local/share}/truenas-widget/setup.sh"')
        for forbidden in ("wss://", "https://", "api-key", "secret-tool", "--"):
            self.assertNotIn(forbidden, cmd)

    def test_befehl_startet_genau_den_starter(self):
        with tempfile.TemporaryDirectory() as tmp:
            starter = Path(tmp) / "data" / "truenas-widget" / "setup.sh"
            starter.parent.mkdir(parents=True)
            starter.write_text('#!/bin/sh\necho "gestartet:$#"\n')
            starter.chmod(0o755)
            proc = subprocess.run(["sh", "-c", js("setupCommand()")[0]],
                                  env={"XDG_DATA_HOME": str(Path(tmp) / "data"), "PATH": os.environ["PATH"]},
                                  capture_output=True, text=True)
        self.assertEqual(proc.stdout.strip(), "gestartet:0")  # ohne Parameter


@unittest.skipUnless(shutil.which("node"), "Node.js nicht installiert")
class SharedAggregateCasesTests(unittest.TestCase):
    """Dieselben Fälle wie im Prüfer (tests/aggregate_cases.json)."""

    def test_gemeinsame_faelle(self):
        cases = json.loads((Path(__file__).parent / "aggregate_cases.json").read_text())["cases"]
        exprs = [f"aggregate({json.dumps(c['systems'])}, ignoredMap({json.dumps(c['ignored'])}))"
                 for c in cases]
        for case, got in zip(cases, js(*exprs)):
            with self.subTest(case["name"]):
                self.assertEqual(got, case["expected"])


if __name__ == "__main__":
    unittest.main()
