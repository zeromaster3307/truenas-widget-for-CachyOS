"""Tests: Auswertung der Mock-Antworten zum Gesamtstatus."""

import unittest

from truenas_widget import checker

from . import fixtures as fx
from .helpers import make_cfg


def status_for(data, denied=()):
    raw = {"alerts": data.get("alert.list"), "apps": data.get("app.query"),
           "system": data.get("update.status"), "denied": []}
    for key, method in (("alerts", "alert.list"), ("apps", "app.query"), ("system", "update.status")):
        if method in denied:
            raw[key] = None
            raw["denied"].append((key, method))
    return checker.build_status(make_cfg(), raw, now=1_700_000_000)


class EvaluateTests(unittest.TestCase):
    def test_alles_ok(self):
        st = status_for(fx.ALL_OK)
        self.assertEqual(st["status"], "ok")
        self.assertEqual(st["status_text"], "OK")
        self.assertEqual(st["alerts"], [])
        self.assertEqual(st["app_updates"], [])
        self.assertFalse(st["system_update"]["available"])
        self.assertEqual(st["system_name"], "Test-NAS")
        self.assertEqual(st["checked_at_epoch"], 1_700_000_000)

    def test_app_updates(self):
        st = status_for(fx.scenario(**{"app.query": [
            fx.app("jellyfin", upgrade=True, version="1.2.3", latest="1.2.4"),
            fx.app("nextcloud", latest="2.0.0", version="2.0.0"),
        ]}))
        self.assertEqual(st["status"], "updates")
        self.assertEqual(st["app_updates"], [
            {"name": "jellyfin", "current": "1.2.3", "new": "1.2.4", "human_version": "10.9.0_1.2.3"}])

    def test_nur_neues_container_image(self):
        st = status_for(fx.scenario(**{"app.query": [
            fx.app("eigene-app", upgrade=True, image_updates=True, custom=True)]}))
        self.assertEqual(st["status"], "updates")
        self.assertIsNone(st["app_updates"][0]["new"])

    def test_systemupdate(self):
        st = status_for(fx.scenario(**{"update.status": fx.update_status("25.10.8")}))
        self.assertEqual(st["status"], "updates")
        self.assertEqual(st["system_update"], {"available": True, "new_version": "25.10.8"})

    def test_update_pruefung_mit_fehler(self):
        st = status_for(fx.scenario(**{"update.status": fx.update_status(error="Keine Verbindung")}))
        self.assertEqual(st["status"], "ok")
        self.assertFalse(st["system_update"]["available"])
        self.assertTrue(any("Update-Prüfung" in p for p in st["problems"]))

    def test_warnung(self):
        st = status_for(fx.scenario(**{"alert.list": [fx.alert("WARNING")],
                                       "update.status": fx.update_status("25.10.8")}))
        self.assertEqual(st["status"], "warning")  # Warnung schlägt Updates
        self.assertEqual(st["alerts"][0]["level"], "WARNING")
        self.assertEqual(st["alerts"][0]["text"], "Pool tank ist DEGRADED")  # HTML entfernt

    def test_kritisch(self):
        st = status_for(fx.scenario(**{"alert.list": [
            fx.alert("WARNING", uuid="w"), fx.alert("CRITICAL", uuid="c")]}))
        self.assertEqual(st["status"], "critical")
        self.assertEqual(st["alerts"][0]["id"], "c")  # kritische zuerst

    def test_error_und_hoeher_gelten_als_kritisch(self):
        for level in ("ERROR", "ALERT", "EMERGENCY"):
            st = status_for(fx.scenario(**{"alert.list": [fx.alert(level)]}))
            self.assertEqual(st["status"], "critical", level)

    def test_quittierter_alert_wird_ignoriert(self):
        st = status_for(fx.scenario(**{"alert.list": [fx.alert("CRITICAL", dismissed=True)]}))
        self.assertEqual(st["status"], "ok")
        self.assertEqual(st["alerts"], [])

    def test_info_alert_wird_ignoriert(self):
        st = status_for(fx.scenario(**{"alert.list": [fx.alert("INFO"), fx.alert("NOTICE", uuid="n")]}))
        self.assertEqual(st["status"], "ok")
        self.assertEqual(st["alerts"], [])

    def test_text_fallback_ohne_formatted(self):
        a = fx.alert("WARNING", formatted=None, text="Nur Vorlage")
        self.assertEqual(checker.evaluate_alerts([a])[0]["text"], "Nur Vorlage")

    def test_fehlende_rechte_ohne_sonstige_befunde(self):
        st = status_for(fx.ALL_OK, denied=("alert.list",))
        # "alles ok" kann ohne Alerts nicht bestätigt werden -> grau/offline
        self.assertEqual(st["status"], "offline")
        self.assertIn("alerts", st["incomplete"])
        self.assertTrue(any("Zugriff verweigert für alert.list" in p for p in st["problems"]))

    def test_fehlende_rechte_mit_befund(self):
        st = status_for(fx.scenario(**{"update.status": fx.update_status("25.10.8")}),
                        denied=("app.query",))
        self.assertEqual(st["status"], "updates")
        self.assertEqual(st["incomplete"], ["apps"])

    def test_offline_status(self):
        st = checker.offline_status(make_cfg(), "Nicht erreichbar", now=1)
        self.assertEqual(st["status"], "offline")
        self.assertEqual(st["offline_reason"], "Nicht erreichbar")


if __name__ == "__main__":
    unittest.main()
