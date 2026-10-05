# SPDX-License-Identifier: GPL-3.0-or-later
"""Mock-Antworten im Format von TrueNAS 25.10.

Feldnamen und Typen aus dem Quellcode TS-25.10.7 übernommen:
  alert.list    -> api/v25_10_0/alert.py, Klasse Alert
  app.query     -> api/v25_10_0/app.py, Klasse AppEntry (hier nur die "select"-Felder)
  update.status -> api/v25_10_0/update.py, Klasse UpdateStatus
Die WERTE sind frei erfunden.
"""

import copy


def alert(level="WARNING", dismissed=False, uuid="a-1", text="Pool %(pool)s ist DEGRADED",
          formatted="Pool <b>tank</b> ist DEGRADED"):
    return {
        "uuid": uuid, "source": "VolumeStatus", "klass": "VolumeStatus", "args": {"pool": "tank"},
        "node": "Controller A", "key": "k-" + uuid,
        "datetime": {"$date": 1700000000000}, "last_occurrence": {"$date": 1700000000000},
        "dismissed": dismissed, "mail": None, "text": text, "id": uuid, "level": level,
        "formatted": formatted, "one_shot": False,
    }


def app(name="jellyfin", upgrade=False, version="1.2.3", latest=None, image_updates=False, custom=False):
    return {
        "name": name, "state": "RUNNING", "version": version,
        "human_version": f"10.9.0_{version}", "latest_version": latest,
        "upgrade_available": upgrade, "image_updates_available": image_updates,
        "custom_app": custom,
    }


def update_status(new_version=None, error=None):
    if error:
        return {"code": "ERROR", "status": None,
                "error": {"errname": "ENONET", "reason": error}, "update_download_progress": None}
    return {
        "code": "NORMAL",
        "status": {
            "current_version": {"train": "TrueNAS-SCALE-Goldeye", "profile": "GENERAL", "matches_profile": True},
            "new_version": None if not new_version else {
                "version": new_version, "manifest": {}, "release_notes": None,
                "release_notes_url": "https://example.invalid/notes",
            },
        },
        "error": None,
        "update_download_progress": None,
    }


ALL_OK = {
    "alert.list": [alert(level="INFO", uuid="i-1", formatted="Info")],
    "app.query": [app("jellyfin", latest="1.2.3"), app("nextcloud", latest="2.0.0", version="2.0.0")],
    "update.status": update_status(),
}


def scenario(**changes):
    data = copy.deepcopy(ALL_OK)
    data.update(changes)
    return data
