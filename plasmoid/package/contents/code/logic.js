.pragma library
// Anzeige-Logik des Widgets (ohne Oberfläche, daher separat testbar).
//
// Das Widget liest NUR die Datei status.json, die der Prüfer schreibt.
// Es spricht nie selbst mit TrueNAS und kennt den API-Key nicht.

// Ab diesem Alter gilt status.json als veraltet (dann: grau, "veraltet").
var STALE_MINUTES = 45;

// Höchstens so viele Alert-Zeilen, danach "+ n weitere".
var MAX_ALERT_LINES = 5;

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

// Liest den Text der Datei. Gibt ein Objekt oder null zurück.
function parseStatus(text) {
    if (!text || !String(text).trim()) {
        return null;
    }
    try {
        var obj = JSON.parse(text);
        return (obj && typeof obj === "object") ? obj : null;
    } catch (e) {
        return null;
    }
}

// Bestimmt, was angezeigt wird. "nowMs" = aktuelle Zeit in Millisekunden.
// Ergebnis: { status, text, reason, stale }
function effective(st, nowMs) {
    if (!st) {
        return { status: "offline", text: "Offline", stale: false,
                 reason: "Noch keine Daten. Läuft der Prüfer (systemd-Timer)?" };
    }
    var status = TEXTS.hasOwnProperty(st.status) ? st.status : "offline";
    var epoch = Number(st.checked_at_epoch || 0) * 1000;
    var ageMin = (nowMs - epoch) / 60000;
    if (!epoch || ageMin > STALE_MINUTES) {
        return { status: "offline", text: "Veraltet", stale: true,
                 reason: "Letzte Prüfung ist älter als " + STALE_MINUTES +
                         " Minuten (Prüfer läuft nicht oder Rechner war im Ruhezustand)." };
    }
    return { status: status, text: TEXTS[status], stale: false,
             reason: status === "offline" ? (st.offline_reason || "Nicht erreichbar.") : "" };
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

// Zeilen für die Vollansicht. Jede Zeile: { kind, text, level }
// kind: "heading" | "app" | "system" | "alert" | "more" | "hint"
function detailLines(st, eff) {
    var lines = [];
    if (!st || eff.status === "ok") {
        return lines;  // alles ok: nur Name + grünes OK, sonst nichts
    }
    if (eff.status === "offline") {
        lines.push({ kind: "hint", text: eff.reason, level: "" });
        if (eff.stale) {
            return lines;
        }
    }
    var apps = st.app_updates || [];
    if (apps.length > 0) {
        lines.push({ kind: "heading", text: "App-Updates", level: "" });
        for (var i = 0; i < apps.length; i++) {
            var a = apps[i];
            var neu = a["new"] ? a["new"] : "neues Image";
            lines.push({ kind: "app", text: a.name + ": " + a.current + " → " + neu, level: "" });
        }
    }
    var su = st.system_update || {};
    if (su.available) {
        lines.push({ kind: "heading", text: "Systemupdate", level: "" });
        lines.push({ kind: "system", text: "Neue Version: " + (su.new_version || "?"), level: "" });
    }
    var alerts = st.alerts || [];
    if (alerts.length > 0) {
        lines.push({ kind: "heading", text: "Warnungen", level: "" });
        var n = Math.min(alerts.length, MAX_ALERT_LINES);
        for (var j = 0; j < n; j++) {
            lines.push({ kind: "alert", text: alerts[j].level + ": " + alerts[j].text,
                         level: alerts[j].severity || "warning" });
        }
        if (alerts.length > MAX_ALERT_LINES) {
            lines.push({ kind: "more", text: "+ " + (alerts.length - MAX_ALERT_LINES) + " weitere", level: "" });
        }
    }
    var problems = st.problems || [];
    for (var k = 0; k < problems.length; k++) {
        lines.push({ kind: "hint", text: problems[k], level: "" });
    }
    return lines;
}

// Kurzinfo für den Tooltip im Panel.
function tooltip(st, eff) {
    if (eff.status === "offline") {
        return eff.reason;
    }
    if (eff.status === "ok") {
        return "Alles in Ordnung.";
    }
    var parts = [];
    var alerts = (st && st.alerts) || [];
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
    var apps = (st && st.app_updates) || [];
    if (apps.length > 0) {
        parts.push(apps.length + (apps.length === 1 ? " App-Update" : " App-Updates"));
    }
    if (st && st.system_update && st.system_update.available) {
        parts.push("Systemupdate " + (st.system_update.new_version || ""));
    }
    return parts.join(", ");
}
