// SPDX-License-Identifier: GPL-3.0-or-later
/*
 * TrueNAS-Status - Plasma-6-Widget (ein oder mehrere TrueNAS-Systeme)
 *
 * Das Widget liest NUR die Datei ~/.cache/truenas-widget/status.json,
 * die der Prüfer (systemd-Timer) schreibt. Es spricht nie selbst mit
 * TrueNAS und kennt keinen API-Key. Es startet genau zwei Befehle
 * (siehe logic.js): die Prüfung und den Einrichtungs-Assistenten.
 *
 * Panel:   farbiger Kreis (grün/gelb/orange/rot/grau) + Tooltip, Klick klappt auf.
 * Details: je System Punkt + Name + Status, darunter nur das Relevante;
 *          unten gemeinsam "Letzte Prüfung" und Knopf "Aktualisieren".
 *          Klick auf ein System öffnet dessen TrueNAS-Oberfläche.
 * Desktop: zeigt immer direkt die Details.
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

    // "offline ignorieren" pro System (Plasma-Einstellung, siehe config/main.xml)
    readonly property var ignored: Logic.ignoredMap(Plasmoid.configuration.ignoreOffline)
    readonly property var eff: Logic.effective(st, nowMs, ignored)
    readonly property var blocks: Logic.blocks(st, eff)
    readonly property var globalLines: Logic.linesWithCheckState(Logic.globalLines(st, eff),
                                                                 checkFailed, notice)
    // Systeme für die Häkchen im Kontextmenü; nur neu setzen, wenn sich etwas
    // ändert (sonst würde das Menü jede Minute neu aufgebaut).
    property var menuSystems: []
    property string menuSystemsKey: ""

    // Zustand der vom Widget ausgelösten Prüfung
    property bool startupDecided: false   // wurde beim Start schon entschieden?
    property bool checking: false         // "Prüfe…" anzeigen
    property bool checkFailed: false      // "Prüfung fehlgeschlagen" anzeigen
    property double epochBeforeCheck: 0   // checked_at_epoch vor dem Start
    property var triggerExit: null        // Exit-Code des Startbefehls
    property double lastTriggerMs: 0      // zusätzlicher Schutz innerhalb des Widgets
    property bool manualCheck: false      // per "Jetzt prüfen"/"Aktualisieren" gestartet?
    property string notice: ""            // kurzer Hinweis, z. B. "Bitte kurz warten"

    readonly property color statusColor: Logic.color(eff.status)

    // Datei lesen über die Shell (Plasma-"executable"-Datenquelle).
    // ${XDG_CACHE_HOME:-$HOME/.cache} entspricht dem Pfad, den der Prüfer benutzt.
    readonly property string readCommand:
        'cat "${XDG_CACHE_HOME:-$HOME/.cache}/truenas-widget/status.json" 2>/dev/null'

    Plasmoid.icon: "network-server"
    Plasmoid.status: (eff.status === "warning" || eff.status === "critical")
                     ? PlasmaCore.Types.NeedsAttentionStatus
                     : PlasmaCore.Types.ActiveStatus

    toolTipMainText: "TrueNAS: " + Logic.headerText(eff, checking)
    toolTipSubText: (notice ? notice + "\n" : "")
                    + (checkFailed ? "Prüfung fehlgeschlagen.\n" : "")
                    + Logic.tooltip(st, eff) + "\n" + Logic.lastCheckText(st, nowMs)

    // Im Panel nur das Icon (Details per Klick), auf dem Desktop IMMER direkt
    // die Detailansicht - unabhängig von der Grösse des Widgets.
    // Muster aus dem KDE-Webbrowser-Widget (kdeplasma-addons, Plasma 6.4):
    // Panel erkennt man an Plasmoid.location (Bildschirmrand).
    readonly property bool inPanel: [PlasmaCore.Types.TopEdge, PlasmaCore.Types.RightEdge,
                                     PlasmaCore.Types.BottomEdge, PlasmaCore.Types.LeftEdge]
                                    .includes(Plasmoid.location)
    preferredRepresentation: inPanel ? compactRepresentation : fullRepresentation
    switchWidth: inPanel ? Number.POSITIVE_INFINITY : 0
    switchHeight: inPanel ? Number.POSITIVE_INFINITY : 0

    function applyStatus(parsed) {
        root.nowMs = Date.now();
        root.st = parsed;
        var ms = Logic.menuSystems(parsed);
        var key = JSON.stringify(ms);
        if (key !== root.menuSystemsKey) {
            root.menuSystemsKey = key;
            root.menuSystems = ms;
        }

        if (!root.startupDecided) {
            // Beim Start EINMAL: fehlt die Datei oder ist sie älter als das
            // Prüfintervall, eine Prüfung anstossen. Ist sie frisch: nichts tun.
            root.startupDecided = true;
            if (Logic.needsCheck(parsed, root.nowMs)) {
                root.startCheck(false);
            }
            return;
        }
        if (root.checking) {
            root.updateCheck(false);
        } else if (root.checkFailed && parsed && Number(parsed.checked_at_epoch) > root.epochBeforeCheck) {
            root.checkFailed = false;  // später doch neue Daten (z. B. vom Timer)
        }
    }

    // manual = true: per "Jetzt prüfen"/"Aktualisieren" ausgelöst. Es gilt dieselbe
    // Sperre (höchstens einmal pro Minute); bei zu frühem Klick erscheint ein Hinweis.
    function startCheck(manual) {
        if (root.checking) {
            return;  // läuft schon
        }
        var now = Date.now();
        if (now - root.lastTriggerMs < Logic.MIN_TRIGGER_SECONDS * 1000) {
            if (manual) {
                root.showNotice(Logic.TOO_SOON_TEXT);
            }
            return;
        }
        root.lastTriggerMs = now;
        root.manualCheck = manual;
        root.notice = "";
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
        if (outcome === "skipped" && root.manualCheck) {
            // z. B. ein anderes Widget hat gerade erst geprüft
            root.showNotice(Logic.TOO_SOON_TEXT);
        }
    }

    function showNotice(text) {
        root.notice = text;
        noticeTimer.restart();
    }

    function refresh() {
        reader.connectSource(readCommand);
    }

    function openUrl(url) {
        // Nur https-Adressen öffnen (kommen aus status.json bzw. der Konfiguration).
        if (typeof url === "string" && url.indexOf("https://") === 0) {
            Qt.openUrlExternally(url);
        }
    }

    function openSetup() {
        setup.connectSource(Logic.setupCommand());
    }

    function setIgnored(id, on) {
        Plasmoid.configuration.ignoreOffline =
            Logic.toggledList(Plasmoid.configuration.ignoreOffline, id, on);
    }

    // ---------------- Rechtsklick-Menü ----------------
    // Belegt im KDE-Quellcode Plasma 6.4 (kdeplasma-addons): contextualActions
    // mit PlasmaCore.Action (applets/timer), checkable (applets/comic),
    // dynamische Einträge per Instantiator + splice (applets/timer).
    Plasmoid.contextualActions: [
        PlasmaCore.Action {
            text: "Jetzt prüfen"
            icon.name: "view-refresh"
            enabled: !root.checking
            onTriggered: root.startCheck(true)
        },
        PlasmaCore.Action {
            id: separatorSystems
            isSeparator: true
        },
        PlasmaCore.Action {
            id: separatorSetup
            isSeparator: true
        },
        PlasmaCore.Action {
            text: "TrueNAS hinzufügen/verwalten…"
            icon.name: "configure"
            onTriggered: root.openSetup()
        }
    ]

    // Je System ein Häkchen "offline ignorieren" (nur bei mehreren Systemen).
    // Wirkt nur auf die Farbe des Panel-Icons; die Zeile des Systems zeigt
    // weiterhin "nicht erreichbar". Falscher Fingerabdruck wird nie ignoriert.
    Instantiator {
        model: root.menuSystems
        delegate: PlasmaCore.Action {
            required property var modelData
            text: modelData.name + ": offline ignorieren"
            checkable: true
            checked: !!root.ignored[modelData.id]
            onTriggered: root.setIgnored(modelData.id, this.checked)
        }
        onObjectAdded: (index, object) => {
            Plasmoid.contextualActions.splice(Plasmoid.contextualActions.indexOf(separatorSetup), 0, object)
        }
        onObjectRemoved: (index, object) => {
            Plasmoid.contextualActions.splice(Plasmoid.contextualActions.indexOf(object), 1)
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

    // Startet NUR den Einrichtungs-Assistenten (siehe logic.js, setupCommand).
    // Meldet sich erst, wenn der Assistent geschlossen wurde.
    P5Support.DataSource {
        id: setup
        engine: "executable"
        connectedSources: []
        onNewData: (sourceName, data) => {
            disconnectSource(sourceName);
            root.refresh();
        }
    }

    // Hinweis "Bitte kurz warten" nach 10 s wieder ausblenden.
    Timer {
        id: noticeTimer
        interval: 10 * 1000
        repeat: false
        onTriggered: root.notice = ""
    }

    // Läuft die Prüfung zu lange: bisheriger Stand + "Prüfung fehlgeschlagen".
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

    // Eine Detailzeile (Überschrift, App, Alert, Hinweis …)
    component DetailLine: PlasmaComponents3.Label {
        required property var line
        Layout.fillWidth: true
        Layout.topMargin: line.kind === "heading" ? Kirigami.Units.smallSpacing : 0
        text: (line.kind === "heading" || line.kind === "hint" ? "" : "• ") + line.text
        font.bold: line.kind === "heading"
        font.italic: line.kind === "hint" || line.kind === "more"
        color: (line.kind === "alert" || line.kind === "notice") ? Logic.color(line.level)
             : (line.kind === "hint" ? Kirigami.Theme.disabledTextColor : Kirigami.Theme.textColor)
        wrapMode: Text.Wrap
        maximumLineCount: line.kind === "alert" ? 2 : 4
        elide: Text.ElideRight
    }

    // ---------------- Detailansicht ----------------
    fullRepresentation: Item {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 12
        Layout.preferredWidth: Kirigami.Units.gridUnit * 20
        Layout.minimumHeight: content.implicitHeight + Kirigami.Units.largeSpacing * 2
        Layout.preferredHeight: content.implicitHeight + Kirigami.Units.largeSpacing * 2

        ColumnLayout {
            id: content
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Kirigami.Units.largeSpacing
            spacing: Kirigami.Units.smallSpacing

            // Hinweise oben: keine Daten / veraltet / nicht eingerichtet,
            // "Prüfung fehlgeschlagen", "Bitte kurz warten"
            Repeater {
                model: root.globalLines
                delegate: DetailLine {
                    required property var modelData
                    line: modelData
                }
            }

            // Je System ein Block. Klick darauf öffnet dessen Oberfläche.
            Repeater {
                model: root.blocks
                delegate: Item {
                    id: block
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: blockColumn.implicitHeight

                    MouseArea {
                        anchors.fill: parent
                        cursorShape: block.modelData.url ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: root.openUrl(block.modelData.url)
                    }

                    ColumnLayout {
                        id: blockColumn
                        anchors.left: parent.left
                        anchors.right: parent.right
                        spacing: Kirigami.Units.smallSpacing

                        // Punkt + Name + Status
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Kirigami.Units.smallSpacing

                            Rectangle {
                                Layout.preferredWidth: Kirigami.Units.iconSizes.small
                                Layout.preferredHeight: Kirigami.Units.iconSizes.small
                                radius: width / 2
                                color: block.modelData.color
                            }
                            PlasmaComponents3.Label {
                                text: block.modelData.name
                                font.bold: true
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            PlasmaComponents3.Label {
                                text: block.modelData.text
                                color: block.modelData.color
                                font.bold: true
                            }
                        }

                        // Darunter nur das Relevante (bei "OK" leer), leicht eingerückt
                        Repeater {
                            model: block.modelData.lines
                            delegate: DetailLine {
                                required property var modelData
                                line: modelData
                                Layout.leftMargin: Kirigami.Units.iconSizes.small + Kirigami.Units.smallSpacing
                            }
                        }
                    }
                }
            }

            // Gemeinsame Fussnote: letzte Prüfung + "Aktualisieren"
            // (+ "Einrichten…", solange noch kein System eingerichtet ist)
            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: Kirigami.Units.smallSpacing
                spacing: Kirigami.Units.smallSpacing

                PlasmaComponents3.Label {
                    Layout.fillWidth: true
                    text: Logic.footerText(root.st, root.nowMs, root.checking)
                    font: Kirigami.Theme.smallFont
                    color: Kirigami.Theme.disabledTextColor
                    horizontalAlignment: Text.AlignLeft
                }
                PlasmaComponents3.ToolButton {
                    visible: root.blocks.length === 0 && !root.eff.stale
                    icon.name: "list-add"
                    text: "Einrichten…"
                    onClicked: root.openSetup()
                }
                PlasmaComponents3.ToolButton {
                    icon.name: "view-refresh"
                    text: root.checking ? "Prüfe…" : "Aktualisieren"
                    enabled: !root.checking
                    onClicked: root.startCheck(true)
                }
            }
        }
    }
}
