/*
 * TrueNAS-Status - Plasma-6-Widget
 *
 * Das Widget liest NUR die Datei ~/.cache/truenas-widget/status.json,
 * die der Prüfer (systemd-Timer) schreibt. Es spricht nie selbst mit
 * TrueNAS und kennt den API-Key nicht.
 *
 * Panel-Ansicht:  farbiger Kreis (grün/gelb/orange/rot/grau) + Tooltip
 * Vollansicht:    Name + Status, darunter nur das Relevante, Fussnote mit Uhrzeit.
 *                 Klick öffnet die TrueNAS-Oberfläche im Browser.
 */
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.components as PlasmaComponents3
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

import "../code/logic.js" as Logic

PlasmoidItem {
    id: root

    // Inhalt von status.json (oder null) und daraus abgeleitete Anzeige
    property var st: null
    property double nowMs: Date.now()
    property var eff: Logic.effective(null, Date.now())
    property var lines: []

    // Zustand der vom Widget ausgelösten Prüfung (nur beim Start, siehe unten)
    property bool startupDecided: false   // wurde beim Start schon entschieden?
    property bool checking: false         // "Prüfe…" anzeigen
    property bool checkFailed: false      // "Prüfung fehlgeschlagen" anzeigen
    property double epochBeforeCheck: 0   // checked_at_epoch vor dem Start
    property var triggerExit: null        // Exit-Code des Startbefehls
    property double lastTriggerMs: 0      // zusätzlicher Schutz innerhalb des Widgets

    readonly property string systemName: (st && st.system_name) ? st.system_name : "TrueNAS"
    readonly property color statusColor: Logic.color(eff.status)
    readonly property string headerText: Logic.headerText(eff, checking)
    readonly property var shownLines: Logic.linesWithCheckState(lines, checkFailed)

    // Datei lesen über die Shell (Plasma-"executable"-Datenquelle).
    // ${XDG_CACHE_HOME:-$HOME/.cache} entspricht dem Pfad, den der Prüfer benutzt.
    readonly property string readCommand:
        'cat "${XDG_CACHE_HOME:-$HOME/.cache}/truenas-widget/status.json" 2>/dev/null'

    Plasmoid.icon: "network-server"
    Plasmoid.status: (eff.status === "warning" || eff.status === "critical")
                     ? PlasmaCore.Types.NeedsAttentionStatus
                     : PlasmaCore.Types.ActiveStatus

    toolTipMainText: systemName + ": " + headerText
    toolTipSubText: (checkFailed ? "Prüfung fehlgeschlagen.\n" : "")
                    + Logic.tooltip(st, eff) + "\n" + Logic.lastCheckText(st, nowMs)

    // Auf dem Desktop (genug Platz) Vollansicht, im Panel nur das Icon.
    switchWidth: Kirigami.Units.gridUnit * 10
    switchHeight: Kirigami.Units.gridUnit * 3

    function applyStatus(parsed) {
        root.nowMs = Date.now();
        root.st = parsed;
        root.eff = Logic.effective(parsed, root.nowMs);
        root.lines = Logic.detailLines(parsed, root.eff);

        if (!root.startupDecided) {
            // Beim Start EINMAL: fehlt die Datei oder ist sie älter als das
            // Prüfintervall, eine Prüfung anstossen. Ist sie frisch: nichts tun.
            root.startupDecided = true;
            if (Logic.needsCheck(parsed, root.nowMs)) {
                root.startCheck();
            }
            return;
        }
        if (root.checking) {
            root.updateCheck(false);
        } else if (root.checkFailed && parsed && Number(parsed.checked_at_epoch) > root.epochBeforeCheck) {
            root.checkFailed = false;  // später doch neue Daten (z. B. vom Timer)
        }
    }

    function startCheck() {
        var now = Date.now();
        if (now - root.lastTriggerMs < Logic.MIN_TRIGGER_SECONDS * 1000) {
            return;
        }
        root.lastTriggerMs = now;
        root.epochBeforeCheck = (root.st && root.st.checked_at_epoch) ? Number(root.st.checked_at_epoch) : 0;
        root.triggerExit = null;
        root.checkFailed = false;
        root.checking = true;
        checkTimeout.restart();
        pollTimer.restart();
        trigger.connectSource(Logic.triggerCommand());
    }

    function updateCheck(timedOut) {
        var outcome = Logic.checkOutcome(root.epochBeforeCheck, root.st, root.triggerExit, timedOut);
        if (outcome === "pending") {
            return;
        }
        root.checking = false;
        root.checkFailed = (outcome === "failed");  // bisheriger Stand bleibt sichtbar
        checkTimeout.stop();
        pollTimer.stop();
    }

    function refresh() {
        reader.connectSource(readCommand);
    }

    function openWebUi() {
        // Nur https-Adressen öffnen (kommt aus status.json bzw. der Konfiguration).
        if (st && typeof st.web_url === "string" && st.web_url.indexOf("https://") === 0) {
            Qt.openUrlExternally(st.web_url);
        }
    }

    // UNGEPRÜFT (nur im KDE-Quellcode nachgelesen, nicht auf echtem Plasma 6 getestet):
    // Die "executable"-Datenquelle aus plasma5support führt den Quellnamen als
    // Shell-Befehl aus (KProcess::setShellCommand, plasma-workspace 6.4,
    // dataengines/executable/executable.cpp) und liefert "exit code", "stdout"
    // und "stderr". Sie gilt in Plasma 6 als veraltet, ist aber noch enthalten.
    // Annahme: Nach disconnectSource() startet ein erneutes connectSource()
    // mit demselben Befehl ihn wieder neu.
    P5Support.DataSource {
        id: reader
        engine: "executable"
        connectedSources: []
        onNewData: (sourceName, data) => {
            disconnectSource(sourceName);  // damit der nächste Aufruf neu liest
            root.applyStatus(Logic.parseStatus(data["stdout"]));
        }
    }

    // Startet NUR "systemctl --user start truenas-widget.service" (siehe logic.js).
    // Die Datenquelle hat keinen eigenen Timeout; den übernimmt checkTimeout.
    P5Support.DataSource {
        id: trigger
        engine: "executable"
        connectedSources: []
        onNewData: (sourceName, data) => {
            disconnectSource(sourceName);
            root.triggerExit = Number(data["exit code"]);
            root.refresh();  // neue status.json lesen; Auswertung in applyStatus
        }
    }

    // Läuft die Prüfung zu lange: bisherigen Stand + "Prüfung fehlgeschlagen".
    Timer {
        id: checkTimeout
        interval: Logic.CHECK_TIMEOUT_SECONDS * 1000
        repeat: false
        onTriggered: if (root.checking) { root.updateCheck(true); }
    }

    // Während der Prüfung alle 5 s nachsehen, ob neue Daten da sind
    // (z. B. wenn ein anderes Widget die Prüfung gestartet hat).
    Timer {
        id: pollTimer
        interval: 5 * 1000
        repeat: true
        onTriggered: root.refresh()
    }

    // Jede Minute neu einlesen (die Datei selbst ändert sich alle ~15 Minuten).
    Timer {
        interval: 60 * 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refresh()
    }

    onExpandedChanged: if (expanded) { root.refresh(); }

    // ---------------- Panel-Ansicht: nur ein farbiges Icon ----------------
    compactRepresentation: MouseArea {
        id: compact
        hoverEnabled: true
        property bool wasExpanded: false
        onPressed: wasExpanded = root.expanded
        onClicked: root.expanded = !wasExpanded

        Accessible.name: root.toolTipMainText
        Accessible.role: Accessible.Button

        Rectangle {
            anchors.centerIn: parent
            width: Math.min(parent.width, parent.height) * 0.8
            height: width
            radius: width / 2
            color: root.statusColor
            opacity: compact.containsMouse ? 0.85 : 1.0

            Text {
                anchors.centerIn: parent
                text: Logic.glyph(root.eff.status)
                color: "white"
                font.bold: true
                font.pixelSize: parent.height * 0.6
            }
        }
    }

    // ---------------- Vollansicht ----------------
    fullRepresentation: Item {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 12
        Layout.preferredWidth: Kirigami.Units.gridUnit * 20
        Layout.minimumHeight: content.implicitHeight + Kirigami.Units.largeSpacing * 2
        Layout.preferredHeight: content.implicitHeight + Kirigami.Units.largeSpacing * 2

        // Klick irgendwo öffnet die TrueNAS-Oberfläche
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.openWebUi()
        }

        ColumnLayout {
            id: content
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Kirigami.Units.largeSpacing
            spacing: Kirigami.Units.smallSpacing

            // Zeile 1: Name + Gesamtstatus
            RowLayout {
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing

                Rectangle {
                    Layout.preferredWidth: Kirigami.Units.iconSizes.small
                    Layout.preferredHeight: Kirigami.Units.iconSizes.small
                    radius: width / 2
                    color: root.statusColor
                }
                PlasmaComponents3.Label {
                    text: root.systemName
                    font.bold: true
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
                PlasmaComponents3.Label {
                    text: root.headerText
                    color: root.statusColor
                    font.bold: true
                }
            }

            // Darunter nur das Relevante (bei "OK" ist diese Liste leer)
            Repeater {
                model: root.shownLines
                delegate: PlasmaComponents3.Label {
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.topMargin: modelData.kind === "heading" ? Kirigami.Units.smallSpacing : 0
                    text: (modelData.kind === "heading" || modelData.kind === "hint" ? "" : "• ")
                          + modelData.text
                    font.bold: modelData.kind === "heading"
                    font.italic: modelData.kind === "hint" || modelData.kind === "more"
                    color: modelData.kind === "alert" ? Logic.color(modelData.level)
                         : (modelData.kind === "hint" ? Kirigami.Theme.disabledTextColor
                                                      : Kirigami.Theme.textColor)
                    wrapMode: Text.Wrap
                    maximumLineCount: modelData.kind === "alert" ? 2 : 4
                    elide: Text.ElideRight
                }
            }

            // Fussnote: Uhrzeit der letzten Prüfung
            PlasmaComponents3.Label {
                Layout.fillWidth: true
                Layout.topMargin: Kirigami.Units.smallSpacing
                text: Logic.lastCheckText(root.st, root.nowMs)
                font: Kirigami.Theme.smallFont
                color: Kirigami.Theme.disabledTextColor
                horizontalAlignment: Text.AlignRight
            }
        }
    }
}
