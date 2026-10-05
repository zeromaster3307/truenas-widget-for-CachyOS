"""Tests: Auswertung der Mock-Antworten zum Gesamtstatus."""

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from truenas_widget import checker

from . import fixtures as fx
from .helpers import make_app, make_cfg

CASES = Path(__file__).parent / "aggregate_cases.json"


def status_for(data, denied=()):
    raw = {"alerts": data.get("alert.list"), "apps": data.get("app.query"),
           "system": data.get("update.status"), "denied": []}
    for key, method in (("alerts", "alert.list"), ("apps", "app.query"), ("system", "update.status")):
        if method in denied:
            raw[key] = None
            raw["denied"].append((key, method))
    return checker.build_entry(make_cfg(), raw)


class EvaluateTests(unittest.TestCase):
    def test_alles_ok(self):
        st = status_for(fx.ALL_OK)
        self.assertEqual(st["status"], "ok")
        self.assertEqual(st["status_text"], "OK")
        self.assertEqual(st["alerts"], [])
        self.assertEqual(st["app_updates"], [])
        self.assertFalse(st["system_update"]["available"])
        self.assertEqual(st["name"], "Test-NAS")
        self.assertEqual(st["id"], "test-nas")
        self.assertEqual(st["web_url"], "https://127.0.0.1:443/")

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
        self.assertEqual(st["offline_kind"], "incomplete")
        self.assertIn("alerts", st["incomplete"])
        self.assertTrue(any("Zugriff verweigert für alert.list" in p for p in st["problems"]))

    def test_fehlende_rechte_mit_befund(self):
        st = status_for(fx.scenario(**{"update.status": fx.update_status("25.10.8")}),
                        denied=("app.query",))
        self.assertEqual(st["status"], "updates")
        self.assertEqual(st["incomplete"], ["apps"])

    def test_offline_eintrag(self):
        st = checker.offline_entry(make_cfg(), "unreachable", "Nicht erreichbar")
        self.assertEqual(st["status"], "offline")
        self.assertEqual(st["offline_kind"], "unreachable")
        self.assertEqual(st["offline_reason"], "Nicht erreichbar")


def entry(status="ok", kind=None, count=0, sid="a"):
    e = checker.system_entry(make_cfg(system_id=sid, name=sid))
    e["status"], e["offline_kind"], e["offline_count"] = status, kind, count
    return e


class CertExpiryNoticeTests(unittest.TestCase):
    NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def entry_for(self, days):
        e = checker.system_entry(make_cfg())
        checker.apply_cert_expiry(e, None if days is None else self.NOW + timedelta(days=days, hours=1), self.NOW)
        return e

    def test_lange_gueltig_kein_hinweis(self):
        e = self.entry_for(200)
        self.assertEqual(e["notices"], [])
        self.assertEqual(e["cert_days_left"], 200)
        self.assertEqual(e["cert_expires"], "2027-04-19")

    def test_30_tage_vorher_hinweis(self):
        e = self.entry_for(30)
        self.assertIn("läuft in 30 Tagen ab (31.10.2026)", e["notices"][0])
        self.assertIn("Fingerabdruck neu prüfen", e["notices"][0])
        self.assertEqual(self.entry_for(31)["notices"], [])

    def test_abgelaufen(self):
        self.assertIn("abgelaufen", self.entry_for(-3)["notices"][0])

    def test_unbekannt(self):
        e = self.entry_for(None)
        self.assertEqual((e["notices"], e["cert_expires"]), ([], None))

    def test_farbe_bleibt(self):
        e = self.entry_for(5)
        self.assertEqual(e["status"], "offline")  # unverändert (system_entry-Standard)
        raw = {"alerts": [], "apps": [], "system": fx.update_status(), "denied": []}
        ok = checker.build_entry(make_cfg(), raw)
        checker.apply_cert_expiry(ok, self.NOW + timedelta(days=5), self.NOW)
        self.assertEqual(ok["status"], "ok")
        self.assertTrue(ok["notices"])


class AggregateTests(unittest.TestCase):
    """Gesamtstatus über mehrere Systeme - gemeinsame Fälle mit dem Widget."""

    def test_gemeinsame_faelle(self):
        cases = json.loads(CASES.read_text())["cases"]
        self.assertGreater(len(cases), 10)
        for case in cases:
            with self.subTest(case["name"]):
                self.assertEqual(checker.aggregate(case["systems"], set(case["ignored"])), case["expected"])

    def test_status_datei_aufbau(self):
        st = checker.build_status(make_app(), [entry("ok")], now=1_700_000_000)
        self.assertEqual(st["schema"], 2)
        self.assertEqual(st["checked_at_epoch"], 1_700_000_000)
        self.assertEqual(st["interval_minutes"], 15)
        self.assertEqual(st["status"], "ok")
        self.assertEqual(len(st["systems"]), 1)
        self.assertIn("Noch kein TrueNAS eingerichtet", checker.build_status(make_app(), [])["reason"])


if __name__ == "__main__":
    unittest.main()
