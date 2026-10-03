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

    readonly property string systemName: (st && st.system_name) ? st.system_name : "TrueNAS"
    readonly property color statusColor: Logic.color(eff.status)

    // Datei lesen über die Shell (Plasma-"executable"-Datenquelle).
    // ${XDG_CACHE_HOME:-$HOME/.cache} entspricht dem Pfad, den der Prüfer benutzt.
    readonly property string readCommand:
        'cat "${XDG_CACHE_HOME:-$HOME/.cache}/truenas-widget/status.json" 2>/dev/null'

    Plasmoid.icon: "network-server"
    Plasmoid.status: (eff.status === "warning" || eff.status === "critical")
                     ? PlasmaCore.Types.NeedsAttentionStatus
                     : PlasmaCore.Types.ActiveStatus

    toolTipMainText: systemName + ": " + eff.text
    toolTipSubText: Logic.tooltip(st, eff) + "\n" + Logic.lastCheckText(st, nowMs)

    // Auf dem Desktop (genug Platz) Vollansicht, im Panel nur das Icon.
    switchWidth: Kirigami.Units.gridUnit * 10
    switchHeight: Kirigami.Units.gridUnit * 3

    function applyStatus(parsed) {
        root.nowMs = Date.now();
        root.st = parsed;
        root.eff = Logic.effective(parsed, root.nowMs);
        root.lines = Logic.detailLines(parsed, root.eff);
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

    P5Support.DataSource {
        id: reader
        engine: "executable"
        connectedSources: []
        onNewData: (sourceName, data) => {
            root.applyStatus(Logic.parseStatus(data["stdout"]));
            disconnectSource(sourceName);  // damit der nächste Aufruf neu liest
        }
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
                    text: root.eff.text
                    color: root.statusColor
                    font.bold: true
                }
            }

            // Darunter nur das Relevante (bei "OK" ist diese Liste leer)
            Repeater {
                model: root.lines
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
