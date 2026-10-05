.pragma library
// Anzeige-Logik des Widgets (ohne Oberfläche, daher separat testbar).
//
// Das Widget liest NUR die Datei status.json, die der Prüfer schreibt.
// Es spricht nie selbst mit TrueNAS und kennt keinen API-Key.
// Es startet genau zwei Befehle, beide ohne Key und ohne Adresse:
//   triggerCommand() - die Prüfung (systemctl --user start truenas-widget.service)
//   setupCommand()   - den Einrichtungs-Assistenten

// Ab diesem Alter gilt status.json als veraltet (dann: grau, "veraltet").
var STALE_MINUTES = 45;

// Höchstens so viele Alert-Zeilen, danach "+ n weitere"
// (pro System; bei mehreren Systemen weniger, damit es übersichtlich bleibt).
var MAX_ALERT_LINES = 5;
var MAX_ALERT_LINES_MULTI = 3;

var COLORS = {
    ok: "#2e9d4f",        // grün
    updates: "#d4a800",   // gelb
    warning: "#ef7d00",   // orange
    critical: "#d62828",  // rot
    offline: "#8a8a8a"    // grau
};

var GLYPHS = {
    ok: "✓",
    updates: "↑",
    warning: "!",
    critical: "✕",
    offline: "?"
};

var TEXTS = {
    ok: "OK",
    updates: "Updates verfügbar",
    warning: "Warnung",
    critical: "Kritisch",
    offline: "Offline"
};

var RANK = { ok: 0, updates: 1, warning: 2, critical: 3 };

// Liest den Text der Datei. Gibt ein Objekt oder null zurück.
// Nur das aktuelle Format (schema 2, Liste "systems") wird akzeptiert;
// eine ältere Datei gilt als "keine Daten" (dann prüft das Widget neu).
function parseStatus(text) {
    if (!text || !String(text).trim()) {
        return null;
    }
    try {
        var obj = JSON.parse(text);
        if (!obj || typeof obj !== "object" || !Array.isArray(obj.systems)) {
            return null;
        }
        return obj;
    } catch (e) {
        return null;
    }
}

function _isIgnored(ignored, id) {
    return !!(ignored && ignored[id]);
}

// Gesamtstatus für Panel-Farbe und Tooltip. DIESELBE Regel wie im Prüfer
// (checker.aggregate); beide werden mit denselben Fällen getestet
// (tests/aggregate_cases.json). Mit "offline ignorieren" pro System:
//  - Ein System: dessen Status (offline = grau).
//  - Mehrere: schlimmster Status der erreichbaren Systeme. Falscher
//    Fingerabdruck zählt immer mindestens als Warnung (nicht ignorierbar).
//    Ein sonst offline System zählt als Warnung, wenn es mindestens 2
//    Prüfungen in Folge fehlte, ein anderes erreichbar ist und es NICHT auf
//    "offline ignorieren" steht. Sind alle offline: grau.
function aggregate(systems, ignored) {
    if (!systems || systems.length === 0) {
        return "offline";
    }
    if (systems.length === 1) {
        return RANK.hasOwnProperty(systems[0].status) ? systems[0].status : "offline";
    }
    var reachable = 0;
    var status = "ok";
    for (var i = 0; i < systems.length; i++) {
        var s = systems[i];
        if (RANK.hasOwnProperty(s.status)) {
            reachable++;
            if (RANK[s.status] > RANK[status]) {
                status = s.status;
            }
        }
    }
    if (reachable === 0) {
        status = "offline";
    }
    for (var j = 0; j < systems.length; j++) {
        var o = systems[j];
        if (RANK.hasOwnProperty(o.status)) {
            continue;
        }
        var counts = o.offline_kind === "fingerprint" ||
            (reachable > 0 && Number(o.offline_count) >= 2 && !_isIgnored(ignored, o.id));
        if (counts && (status === "offline" || RANK[status] < RANK.warning)) {
            status = "warning";
        }
    }
    return status;
}

// Bestimmt, was insgesamt angezeigt wird. "nowMs" = aktuelle Zeit in ms,
// "ignored" = { id: true } für Systeme mit "offline ignorieren".
// Ergebnis: { status, text, reason, stale }
function effective(st, nowMs, ignored) {
    if (!st) {
        return { status: "offline", text: "Offline", stale: false,
                 reason: "Noch keine Daten. Läuft der Prüfer (systemd-Timer)?" };
    }
    var epoch = Number(st.checked_at_epoch || 0) * 1000;
    var ageMin = (nowMs - epoch) / 60000;
    if (!epoch || ageMin > STALE_MINUTES) {
        return { status: "offline", text: "Veraltet", stale: true,
                 reason: "Letzte Prüfung ist älter als " + STALE_MINUTES +
                         " Minuten (Prüfer läuft nicht oder Rechner war im Ruhezustand)." };
    }
    var systems = st.systems || [];
    if (systems.length === 0) {
        return { status: "offline", text: "Nicht eingerichtet", stale: false,
                 reason: st.reason || "Noch kein TrueNAS eingerichtet. Rechtsklick → „TrueNAS hinzufügen/verwalten…“." };
    }
    var status = aggregate(systems, ignored);
    var reason = "";
    if (status === "offline") {
        reason = systems.length === 1 ? (systems[0].offline_reason || "Nicht erreichbar.")
                                      : "Kein TrueNAS erreichbar.";
    }
    return { status: status, text: TEXTS[status], stale: false, reason: reason };
}

function color(status) {
    return COLORS[status] || COLORS.offline;
}

function glyph(status) {
    return GLYPHS[status] || GLYPHS.offline;
}

// Uhrzeit der letzten Prüfung als "HH:MM" (bzw. "TT.MM. HH:MM", wenn nicht heute).
function lastCheckText(st, nowMs) {
    if (!st || !st.checked_at_epoch) {
        return "Letzte Prüfung: –";
    }
    var d = new Date(Number(st.checked_at_epoch) * 1000);
    var now = new Date(nowMs);
    var hh = ("0" + d.getHours()).slice(-2);
    var mm = ("0" + d.getMinutes()).slice(-2);
    var text = hh + ":" + mm;
    if (d.toDateString() !== now.toDateString()) {
        text = ("0" + d.getDate()).slice(-2) + "." + ("0" + (d.getMonth() + 1)).slice(-2) + ". " + text;
    }
    return "Letzte Prüfung: " + text;
}

// Detailzeilen EINES Systems. Jede Zeile: { kind, text, level }
// kind: "heading" | "app" | "system" | "alert" | "more" | "hint"
// maxAlerts: bei einem System 5, bei mehreren 3 Alert-Zeilen.
function systemLines(s, maxAlerts) {
    var lines = [];
    if (!s || s.status === "ok") {
        return lines;  // alles ok: nur Name + grünes OK, sonst nichts
    }
    if (s.status === "offline") {
        lines.push({ kind: "hint", text: s.offline_reason || "Nicht erreichbar.", level: "" });
    }
    var apps = s.app_updates || [];
    if (apps.length > 0) {
        lines.push({ kind: "heading", text: "App-Updates", level: "" });
        for (var i = 0; i < apps.length; i++) {
            var a = apps[i];
            var neu = a["new"] ? a["new"] : "neues Image";
            lines.push({ kind: "app", text: a.name + ": " + a.current + " → " + neu, level: "" });
        }
    }
    var su = s.system_update || {};
    if (su.available) {
        lines.push({ kind: "heading", text: "Systemupdate", level: "" });
        lines.push({ kind: "system", text: "Neue Version: " + (su.new_version || "?"), level: "" });
    }
    var alerts = s.alerts || [];
    if (alerts.length > 0) {
        lines.push({ kind: "heading", text: "Warnungen", level: "" });
        var n = Math.min(alerts.length, maxAlerts);
        for (var j = 0; j < n; j++) {
            lines.push({ kind: "alert", text: alerts[j].level + ": " + alerts[j].text,
                         level: alerts[j].severity || "warning" });
        }
        if (alerts.length > maxAlerts) {
            lines.push({ kind: "more", text: "+ " + (alerts.length - maxAlerts) + " weitere", level: "" });
        }
    }
    var problems = s.problems || [];
    for (var k = 0; k < problems.length; k++) {
        lines.push({ kind: "hint", text: problems[k], level: "" });
    }
    return lines;
}

// Blöcke für die Vollansicht: je System { id, name, status, text, color, url, lines }.
// Leer, wenn keine Daten, veraltet oder nichts eingerichtet (dann steht der
// Grund in globalLines()).
function blocks(st, eff) {
    if (!st || eff.stale || !st.systems || st.systems.length === 0) {
        return [];
    }
    var maxAlerts = st.systems.length > 1 ? MAX_ALERT_LINES_MULTI : MAX_ALERT_LINES;
    var result = [];
    for (var i = 0; i < st.systems.length; i++) {
        var s = st.systems[i];
        var status = RANK.hasOwnProperty(s.status) ? s.status : "offline";
        result.push({
            id: s.id, name: s.name || s.id, status: status, text: TEXTS[status],
            color: color(status),
            url: (typeof s.web_url === "string" && s.web_url.indexOf("https://") === 0) ? s.web_url : "",
            lines: systemLines(s, maxAlerts)
        });
    }
    return result;
}

// Zeilen über den Systemen (keine Daten, veraltet, nicht eingerichtet).
function globalLines(st, eff) {
    if (!st || eff.stale || !st.systems || st.systems.length === 0) {
        return [{ kind: "hint", text: eff.reason, level: "" }];
    }
    return [];
}

function _summary(s) {
    if (s.status === "offline") {
        return s.offline_kind === "fingerprint" ? "Fingerabdruck stimmt nicht" : "nicht erreichbar";
    }
    if (s.status === "ok") {
        return "OK";
    }
    var parts = [];
    var alerts = s.alerts || [];
    var crit = 0;
    for (var i = 0; i < alerts.length; i++) {
        if (alerts[i].severity === "critical") {
            crit++;
        }
    }
    if (crit > 0) {
        parts.push(crit + " kritisch");
    }
    if (alerts.length - crit > 0) {
        parts.push((alerts.length - crit) + (alerts.length - crit === 1 ? " Warnung" : " Warnungen"));
    }
    var apps = s.app_updates || [];
    if (apps.length > 0) {
        parts.push(apps.length + (apps.length === 1 ? " App-Update" : " App-Updates"));
    }
    if (s.system_update && s.system_update.available) {
        parts.push("Systemupdate " + (s.system_update.new_version || ""));
    }
    return parts.length ? parts.join(", ") : TEXTS[s.status] || "";
}

// Kurzinfo für den Tooltip im Panel.
function tooltip(st, eff) {
    if (!st || eff.stale || !st.systems || st.systems.length === 0) {
        return eff.reason;
    }
    if (st.systems.length === 1) {
        var s = st.systems[0];
        if (s.status === "offline") {
            return s.offline_reason || "Nicht erreichbar.";
        }
        return s.status === "ok" ? "Alles in Ordnung." : _summary(s);
    }
    var lines = [];
    for (var i = 0; i < st.systems.length; i++) {
        lines.push((st.systems[i].name || st.systems[i].id) + ": " + _summary(st.systems[i]));
    }
    return lines.join("\n");
}

// Systeme für die Häkchen "offline ignorieren" im Kontextmenü
// (nur bei mehreren Systemen sinnvoll). Ergebnis: [{ id, name }]
function menuSystems(st) {
    if (!st || !st.systems || st.systems.length < 2) {
        return [];
    }
    var result = [];
    for (var i = 0; i < st.systems.length; i++) {
        result.push({ id: st.systems[i].id, name: st.systems[i].name || st.systems[i].id });
    }
    return result;
}

// Wandelt die gespeicherte Liste (Plasma-Einstellung "ignoreOffline")
// in { id: true } um, und schaltet ein System um.
function ignoredMap(list) {
    var map = {};
    if (list) {
        for (var i = 0; i < list.length; i++) {
            map[String(list[i])] = true;
        }
    }
    return map;
}

function toggledList(list, id, on) {
    var result = [];
    if (list) {
        for (var i = 0; i < list.length; i++) {
            if (String(list[i]) !== id) {
                result.push(String(list[i]));
            }
        }
    }
    if (on) {
        result.push(id);
    }
    return result;
}

// ------------------------------------------------------------------------
// Prüfung beim Start des Widgets auslösen
// ------------------------------------------------------------------------
//
// SICHERHEIT: Das Widget startet NUR den Dienst "truenas-widget.service"
// über "systemctl --user start", ohne Parameter. Es übergibt keinen Key,
// keine Adresse und hat selbst keinen Zugriff auf TrueNAS. Die eigentliche
// Abfrage macht wie immer der Prüfer (Python) im Dienst.

// Wird benutzt, wenn status.json kein Intervall enthält (ältere Datei).
var DEFAULT_INTERVAL_MINUTES = 15;

// Nie öfter als einmal pro Minute auslösen.
var MIN_TRIGGER_SECONDS = 60;

// Danach gilt die Prüfung als fehlgeschlagen. Der Dienst selbst bricht nach
// 120 s ab (TimeoutStartSec in truenas-widget.service), plus Reserve.
var CHECK_TIMEOUT_SECONDS = 150;

// Rückgabewerte des Startbefehls, wenn er bewusst NICHTS gestartet hat:
//   75 = ein anderes Widget hat gerade eine Prüfung laufen -> auf deren Ergebnis warten
//   76 = der letzte Start ist keine 60 s her (und läuft nicht mehr) -> nichts zu warten
var SKIPPED_EXIT_CODE = 75;
var TOO_SOON_EXIT_CODE = 76;

// true = status.json fehlt/ist unlesbar oder älter als das Prüfintervall.
function needsCheck(st, nowMs) {
    if (!st || !st.checked_at_epoch) {
        return true;
    }
    var interval = Number(st.interval_minutes) > 0 ? Number(st.interval_minutes) : DEFAULT_INTERVAL_MINUTES;
    var ageMs = nowMs - Number(st.checked_at_epoch) * 1000;
    return ageMs > interval * 60000;
}

// Der Shell-Befehl, den das Widget (einmalig beim Start) ausführt.
//
// Schutz gegen Mehrfachstarts (Plasma-Neustart, mehrere Widgets):
//  - flock: läuft schon ein Start eines anderen Widgets, sofort aufhören.
//  - Zeitstempel-Datei: liegt der letzte Start keine 60 s zurück, aufhören.
// Beide Dateien liegen in ~/.cache/truenas-widget/ und enthalten nur eine Uhrzeit.
// "systemctl --user start" wartet, bis der Dienst (Type=oneshot) fertig ist.
function triggerCommand() {
    return 'd="${XDG_CACHE_HOME:-$HOME/.cache}/truenas-widget"; ' +
        'mkdir -p "$d" || exit 1; ' +
        'exec 9>"$d/widget-trigger.lock"; ' +
        'flock -n 9 || exit ' + SKIPPED_EXIT_CODE + '; ' +
        'now=$(date +%s); ' +
        'last=$(cat "$d/widget-trigger.stamp" 2>/dev/null); ' +
        'case "$last" in ""|*[!0-9]*) last=0;; esac; ' +
        'if [ "$last" -le "$now" ] && [ $((now - last)) -lt ' + MIN_TRIGGER_SECONDS + ' ]; then exit ' +
        TOO_SOON_EXIT_CODE + '; fi; ' +
        'echo "$now" > "$d/widget-trigger.stamp"; ' +
        'exec systemctl --user start truenas-widget.service';
}

// Wie steht eine laufende Prüfung?
//   epochBefore : checked_at_epoch VOR dem Start (0, wenn keine Datei)
//   st          : zuletzt gelesene status.json (oder null)
//   triggerExit : Exit-Code des Startbefehls, null solange er noch läuft
//   timedOut    : true, wenn CHECK_TIMEOUT_SECONDS abgelaufen sind
// Ergebnis: "done" (neue Daten da), "failed", "skipped" (zu kurz nach dem
// letzten Start, nichts gestartet) oder "pending" (weiter warten)
function checkOutcome(epochBefore, st, triggerExit, timedOut) {
    var epoch = (st && st.checked_at_epoch) ? Number(st.checked_at_epoch) : 0;
    if (epoch > epochBefore) {
        return "done";
    }
    if (triggerExit === TOO_SOON_EXIT_CODE) {
        return "skipped";
    }
    if (timedOut) {
        return "failed";
    }
    if (triggerExit !== null && triggerExit !== undefined &&
            triggerExit !== 0 && triggerExit !== SKIPPED_EXIT_CODE) {
        return "failed";  // z. B. Dienst nicht installiert
    }
    return "pending";
}

// Text neben dem Namen: während der Prüfung "Prüfe…", sonst der Status.
function headerText(eff, checking) {
    return checking ? "Prüfe…" : eff.text;
}

// Hinweis, wenn "Jetzt prüfen" zu früh geklickt wurde.
var TOO_SOON_TEXT = "Bitte kurz warten – höchstens eine Prüfung pro Minute.";

// Zeilen der Vollansicht, ergänzt um die Hinweise zur Prüfung:
//   failed : "Prüfung fehlgeschlagen" (bisheriger Stand bleibt sichtbar)
//   notice : kurzer Hinweis, z. B. TOO_SOON_TEXT (oder "")
function linesWithCheckState(lines, failed, notice) {
    var extra = [];
    if (notice) {
        extra.push({ kind: "hint", text: notice, level: "" });
    }
    if (failed) {
        extra.push({ kind: "hint", text: "Prüfung fehlgeschlagen – bisheriger Stand wird angezeigt.", level: "" });
    }
    return extra.length ? extra.concat(lines) : lines;
}

// Fussnote: während der Prüfung "Prüfe…", sonst die Uhrzeit der letzten Prüfung.
function footerText(st, nowMs, checking) {
    return checking ? "Prüfe…" : lastCheckText(st, nowMs);
}

// ------------------------------------------------------------------------
// Einrichtungs-Assistent starten
// ------------------------------------------------------------------------
//
// SICHERHEIT: startet NUR den von install.sh angelegten Starter
// ~/.local/share/truenas-widget/setup.sh, ohne Parameter. Der Assistent
// läuft als eigenes Programm mit eigenen Fenstern; Key und Adressen gibt
// man dort ein - das Widget sieht davon nichts. Doppelstarts verhindert
// der Assistent selbst (Sperrdatei).
function setupCommand() {
    return 'exec "${XDG_DATA_HOME:-$HOME/.local/share}/truenas-widget/setup.sh"';
}
