import QtQuick
import QtQuick.Controls
Item {
    id: root
    property var entries: []
    property string selectedId: ""
    property string placeholder: "Выберите…"
    property string iconName: "search"
    signal selected(string id)
    implicitHeight: Theme.controlHeight
    implicitWidth: 240
    activeFocusOnTab: true
    Accessible.name: selectedText()
    Accessible.role: Accessible.ComboBox
    function openList() { search.text=""; popup.open(); search.forceActiveFocus() }
    function choose(index) { if (index>=0 && index<filtered.count) { root.selected(filtered.get(index).key); popup.close(); root.forceActiveFocus() } }
    Keys.onReturnPressed: openList()
    Keys.onEnterPressed: openList()
    Keys.onSpacePressed: openList()
    Keys.onDownPressed: openList()
    Keys.onEscapePressed: popup.close()
    function nameOf(e) { return String(e && (e.name || e.title || e.label || e.path || e.id) || "") }
    function idOf(e) { return String(e && (e.id || e.path || e.name || e.title) || "") }
    function selectedText() {
        for (let i=0; i<entries.length; i++) if (idOf(entries[i]) === selectedId) return nameOf(entries[i])
        return placeholder
    }
    Rectangle { anchors.fill: parent; color: Theme.panel2; radius: 6; border.color: popup.opened ? Theme.violet : Theme.border }
    Row {
        anchors.fill: parent; anchors.leftMargin: 11; anchors.rightMargin: 10; spacing: 10
        StudioIcon { name: root.iconName; anchors.verticalCenter: parent.verticalCenter }
        Text { width: parent.width-64; anchors.verticalCenter: parent.verticalCenter; text: root.selectedText(); color: Theme.text; elide: Text.ElideRight; font.pixelSize: 14; font.family: "Segoe UI" }
        StudioIcon { name: "chevron-down"; anchors.verticalCenter: parent.verticalCenter; width: 14; height: 14 }
    }
    MouseArea { anchors.fill: parent; onClicked: root.openList() }
    Popup {
        id: popup
        y: root.height + 4; width: root.width; height: Math.min(320, Math.max(1,filtered.count)*34 + 50)
        padding: 7; modal: false; closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: Theme.panel2; border.color: Theme.border; radius: 6 }
        contentItem: Column {
            spacing: 6
            StudioField { id: search; width: parent.width; placeholderText: "Поиск…"
                Keys.onDownPressed: { options.currentIndex=0; options.forceActiveFocus() }
                Keys.onReturnPressed: root.choose(0)
                Keys.onEnterPressed: root.choose(0)
                Keys.onEscapePressed: popup.close()
            }
            ListView {
                id: options
                width: parent.width; height: Math.min(238, Math.max(1,filtered.count)*34); clip: true; model: filtered
                keyNavigationWraps: true
                Keys.onReturnPressed: root.choose(currentIndex)
                Keys.onEnterPressed: root.choose(currentIndex)
                Keys.onEscapePressed: popup.close()
                delegate: ItemDelegate {
                    id: option
                    required property string label
                    required property string key
                    highlighted: ListView.isCurrentItem
                    width: ListView.view.width; height: 34; text: label
                    onClicked: { root.selected(key); popup.close() }
                    contentItem: Text { text: option.text; color: Theme.text; font.pixelSize: 13; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
                    background: Rectangle { color: option.hovered || option.highlighted ? Theme.violet2 : "transparent"; radius: 4 }
                }
                Label { anchors.centerIn: parent; visible: filtered.count===0; text: "Ничего не найдено"; color: Theme.muted }
            }
        }
    }
    ListModel { id: filtered }
    function rebuild() {
        filtered.clear()
        let q=search.text.toLowerCase()
        for (let i=0; i<entries.length; i++) {
            let e=entries[i], n=nameOf(e)
            if (n.toLowerCase().indexOf(q)>=0) filtered.append({label:n, key:idOf(e)})
        }
    }
    onEntriesChanged: rebuild()
    Connections { target: search; function onTextChanged() { root.rebuild() } }
    Component.onCompleted: rebuild()
}
