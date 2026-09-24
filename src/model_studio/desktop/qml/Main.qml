import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

ApplicationWindow {
    id: root
    width: 1488; height: 1060
    minimumWidth: 1100; minimumHeight: 760
    visible: true
    title: "Модельная студия"
    color: Theme.bg
    palette.window: Theme.bg
    palette.windowText: Theme.text
    palette.base: Theme.panel2
    palette.text: Theme.text
    palette.button: Theme.panel2
    palette.buttonText: Theme.text
    palette.highlight: Theme.violet
    palette.highlightedText: Theme.text
    property var bridge: studio
    property int currentPage: bridge.page || 0
    property bool closePrepared: false
    property string lastNotice: ""
    property string noticeText: ""
    property bool noticeVisible: false
    function val(obj, key, fallback) { let x=obj && obj[key]; return x === undefined || x === null || x === "" ? fallback : x }
    function fmt(x, suffix) { return x === undefined || x === null || x === "" ? "Нет данных" : String(x) + (suffix || "") }
    function contextLabel(x) { let n=Number(x); return x===undefined || x===null || x==="" ? "Нет данных" : Number.isFinite(n) && n>=1024 ? (n%1024===0 ? n/1024 : Math.round(n/1000))+"K" : String(x) }
    function running() { return val(bridge.session,"status","") === "ready" }
    function statusText() {
        if (bridge.busy && val(bridge.research,"status","")==="running") return "Занята исследованием"
        let s=val(bridge.session,"status","stopped")
        if (bridge.busy && s==="ready") return "Занята операцией"
        return ({ready:"Работает", starting:"Запускается", stopping:"Останавливается", failed:"Ошибка запуска", disconnected:"Нет соединения", stopped:"Сервер остановлен"})[s] || "Состояние неизвестно"
    }
    function statusColor() { let s=val(bridge.session,"status","stopped"); return s==="ready" ? Theme.green : ["failed","disconnected"].indexOf(s)>=0 ? Theme.red : Theme.muted }
    function modelName() { return val(bridge.session,"model_name",val(bridge.selectedModel,"name","Модель не выбрана")) }
    function contextText() { return contextLabel(val(bridge.session,"context",null)) }
    function navigate(n) { bridge.page=n; currentPage=n }
    onCurrentPageChanged: if (bridge.page !== currentPage) bridge.page = currentPage

    ColumnLayout {
        anchors.fill: parent; spacing: 0
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 59; color: Theme.panel
            border.color: Theme.border; border.width: 1
            Row { anchors.verticalCenter: parent.verticalCenter; anchors.left: parent.left; anchors.leftMargin: 32; spacing: 20
                Rectangle { width: 29; height: 29; radius: 4; color: Theme.violet
                    Text { anchors.centerIn: parent; text: "М"; font.bold: true; font.pixelSize: 20; color: Theme.text }
                }
                Text { anchors.verticalCenter: parent.verticalCenter; text: "Модельная студия"; color: Theme.text; font.pixelSize: 20; font.family: "Segoe UI" }
            }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 0
            Rectangle {
                Layout.preferredWidth: 230; Layout.fillHeight: true; color: Theme.panel
                border.color: Theme.border; border.width: 1
                Column { anchors.fill: parent; anchors.margins: 13; anchors.topMargin: 24; spacing: 9
                    Repeater {
                        model: [
                            { label:"Запуск", icon:"player-play" }, { label:"Чат", icon:"message" },
                            { label:"Модели", icon:"cube" }, { label:"Исследование", icon:"chart-bar" },
                            { label:"Настройки", icon:"settings" }
                        ]
                        delegate: Rectangle {
                            required property var modelData
                            required property int index
                            width: 204; height: 54; radius: 8
                            color: currentPage===index ? "#202648" : sideMouse.containsMouse ? "#1d2a3a" : "transparent"
                            Rectangle { width: 4; height: parent.height; anchors.left: parent.left; radius: 2; visible: currentPage===index; color: "#a578ff" }
                            Row { anchors.verticalCenter: parent.verticalCenter; anchors.left: parent.left; anchors.leftMargin: 20; spacing: 20
                                StudioIcon { name: modelData.icon; width: 26; height: 26 }
                                Text { anchors.verticalCenter: parent.verticalCenter; text: modelData.label; color: Theme.text; font.pixelSize: 17; font.family: "Segoe UI" }
                            }
                            MouseArea { id: sideMouse; anchors.fill: parent; hoverEnabled: true; onClicked: root.navigate(index) }
                        }
                    }
                }
            }
            StackLayout {
                Layout.fillWidth: true; Layout.fillHeight: true
                currentIndex: root.currentPage
                LaunchPage { bridge: root.bridge; host: root }
                ChatPage { bridge: root.bridge; host: root }
                ModelsPage { bridge: root.bridge; host: root }
                ResearchPage { bridge: root.bridge; host: root }
                SettingsPage { bridge: root.bridge; host: root }
            }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 57; color: Theme.panel
            border.color: Theme.border; border.width: 1
            Row { anchors.left: parent.left; anchors.leftMargin: 34; anchors.verticalCenter: parent.verticalCenter; spacing: 14
                Rectangle { width: 17; height: 17; radius: 9; anchors.verticalCenter: parent.verticalCenter; color: root.statusColor() }
                Text { text: root.running() ? root.modelName() : root.statusText(); color: Theme.text; font.pixelSize: 14; font.family: "Segoe UI" }
                Rectangle { width: 1; height: 19; color: Theme.border }
                Text { text: root.bridge.busy ? root.statusText() : root.running() ? "Работает" : root.val(root.bridge.session,"status","stopped")==="stopped" ? "Выберите модель и нажмите «Запустить модель»." : root.statusText(); color: root.statusColor(); font.pixelSize: 14; font.family: "Segoe UI"; width: Math.min(root.width-570,650); elide: Text.ElideRight }
                Text { visible: root.running(); text: "  •  " + root.contextText(); color: Theme.muted; font.pixelSize: 14 }
            }
            Row { anchors.right: parent.right; anchors.rightMargin: 35; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                Text { text: "Текущая сессия"; color: Theme.muted; font.pixelSize: 14 }
                StudioIcon { name: "info-circle"; width: 17; height: 17 }
            }
        }
    }
    Dialog {
        id: errorDialog; modal: true; title: "Ошибка"; standardButtons: Dialog.Ok
        anchors.centerIn: parent; width: Math.min(540, root.width-80)
        onAccepted: bridge.clearError()
        contentItem: Text { text: bridge.error || ""; color: Theme.text; wrapMode: Text.WordWrap; font.pixelSize: 15 }
        background: Rectangle { color: Theme.panel2; border.color: Theme.border; radius: 8 }
        onVisibleChanged: if (!visible && bridge.error) bridge.clearError()
    }
    Rectangle {
        z: 50; visible: root.noticeVisible
        anchors.horizontalCenter: parent.horizontalCenter; anchors.bottom: parent.bottom; anchors.bottomMargin: 70
        width: Math.min(660,root.width-80); height: noticeLabel.implicitHeight+27; radius: 8
        color: Theme.panel2; border.color: Theme.violet
        Text { id: noticeLabel; anchors.fill: parent; anchors.margins: 13; text: root.noticeText; color: Theme.text; font.pixelSize: 15; wrapMode: Text.WordWrap }
        MouseArea { anchors.fill: parent; onClicked: root.noticeVisible=false }
    }
    Timer { id: toastTimer; interval: 5000; repeat: false; onTriggered: root.noticeVisible=false }
    Dialog {
        id: closeDialog; modal: true; title: "Завершить работу?"; standardButtons: Dialog.Yes | Dialog.Cancel
        anchors.centerIn: parent; width: 440
        onAccepted: { if (root.bridge.requestClose(true)) { root.closePrepared=true; root.close() } }
        contentItem: Text { text: "Активная сессия или задание будет остановлено."; color: Theme.text; wrapMode: Text.WordWrap }
        background: Rectangle { color: Theme.panel2; border.color: Theme.border; radius: 8 }
    }
    Dialog {
        id: operationDialog; property string message: ""; modal: true; title: "Подтвердите действие"; standardButtons: Dialog.Yes | Dialog.Cancel
        anchors.centerIn: parent; width: 470
        onAccepted: root.bridge.confirmOperation(true)
        onRejected: root.bridge.confirmOperation(false)
        contentItem: Text { text: operationDialog.message; color: Theme.text; wrapMode: Text.WordWrap; font.pixelSize: 16 }
        background: Rectangle { color: Theme.panel2; border.color: Theme.border; radius: 8 }
    }
    onClosing: function(close) {
        if (closePrepared) { close.accepted=true; return }
        close.accepted=false
        if (bridge.needsCloseConfirmation) closeDialog.open()
        else if (bridge.requestClose(false)) { closePrepared=true; root.close() }
    }
    Connections { target: bridge; function onChanged() { if (root.bridge.error && !errorDialog.visible) errorDialog.open(); if (root.currentPage!==root.bridge.page) root.currentPage=root.bridge.page; if (root.bridge.notice && root.bridge.notice!==root.lastNotice) { root.lastNotice=root.bridge.notice; root.noticeText=root.bridge.notice; root.noticeVisible=true; toastTimer.restart() } } }
    Connections { target: bridge; function onConfirmationRequested(message) { operationDialog.message=message; operationDialog.open() } }
    Connections { target: bridge; function onCloseReady() { root.closePrepared=true; root.close() } }
    Component.onCompleted: { if (bridge.notice) { lastNotice=bridge.notice; noticeText=bridge.notice; noticeVisible=true; toastTimer.start() } }
}
