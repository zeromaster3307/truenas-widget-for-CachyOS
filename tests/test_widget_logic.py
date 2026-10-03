"""Tests: Anzeige-Logik des Plasma-Widgets (logic.js), ausgeführt mit Node.js.

Die QML-Oberfläche selbst kann ohne Plasma nicht getestet werden; die
Entscheidungen (Farbe, veraltet, welche Zeilen, max. 5 Alerts) stecken aber
alle in logic.js und werden hier geprüft. Ohne Node.js wird übersprungen.
"""

import json
import shutil
import subprocess
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
  const eff = ctx.effective(st, c.now);
  return { eff, color: ctx.color(eff.status), lines: ctx.detailLines(st, eff),
           tooltip: ctx.tooltip(st, eff), last: ctx.lastCheckText(st, c.now) };
});
process.stdout.write(JSON.stringify(out));
"""

NOW = 1_700_000_000


def st(status="ok", **kw):
    base = {"status": status, "system_name": "NAS", "checked_at_epoch": NOW,
            "app_updates": [], "system_update": {"available": False, "new_version": None},
            "alerts": [], "problems": [], "offline_reason": None}
    base.update(kw)
    return base


@unittest.skipUnless(shutil.which("node"), "Node.js nicht installiert")
class WidgetLogicTests(unittest.TestCase):
    def run_cases(self, cases):
        proc = subprocess.run(["node", "-e", RUNNER, str(LOGIC)], input=json.dumps(cases),
                              capture_output=True, text=True, check=True)
        return json.loads(proc.stdout)

    def one(self, status_obj, now_offset_min=1, text=None):
        case = {"now": (NOW + now_offset_min * 60) * 1000}
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

    def test_farben(self):
        for status, color in (("updates", "#d4a800"), ("warning", "#ef7d00"),
                              ("critical", "#d62828"), ("offline", "#8a8a8a")):
            self.assertEqual(self.one(st(status))["color"], color, status)

    def test_veraltet_nach_45_minuten(self):
        self.assertEqual(self.one(st("ok"), now_offset_min=44)["eff"]["status"], "ok")
        r = self.one(st("ok"), now_offset_min=46)
        self.assertEqual(r["eff"]["status"], "offline")
        self.assertEqual(r["eff"]["text"], "Veraltet")

    def test_keine_datei(self):
        r = self.one(None, text="")
        self.assertEqual(r["eff"]["status"], "offline")
        self.assertIn("Noch keine Daten", r["eff"]["reason"])

    def test_kaputte_datei(self):
        self.assertEqual(self.one(None, text="{kaputt")["eff"]["status"], "offline")

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


if __name__ == "__main__":
    unittest.main()
