import QtQuick
import QtQuick.Controls
import "."

Rectangle {
    id: root
    property var attachment
    property bool removable: false
    signal removeRequested()
    width: 112; height: 92; radius: 6
    color: Theme.panel; border.color: Theme.border
    Image {
        id: thumbnail
        anchors { top: parent.top; left: parent.left; right: parent.right; margins: 5 }
        height: 62; sourceSize: Qt.size(204, 124)
        fillMode: Image.PreserveAspectFit; asynchronous: true
        source: root.attachment ? (root.attachment.preview_url || "") : ""
    }
    Text {
        anchors.centerIn: thumbnail
        visible: thumbnail.status === Image.Error
        text: "Файл недоступен"; color: Theme.muted; font.pixelSize: 11
    }
    Text {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 5 }
        text: root.attachment ? root.attachment.name : ""
        color: Theme.muted; font.pixelSize: 11; elide: Text.ElideMiddle
    }
    StudioButton {
        anchors { top: parent.top; right: parent.right; margins: 2 }
        visible: root.removable; text: ""; iconName: "x"
        implicitWidth: 24; buttonHeight: 24
        Accessible.name: "Убрать изображение"
        onClicked: root.removeRequested()
    }
}
