import QtQuick
import QtQuick.Controls
Button {
    id: control
    property string iconName: ""
    property bool primary: false
    property bool danger: false
    property bool subtle: false
    property int buttonHeight: 48
    implicitHeight: buttonHeight
    implicitWidth: Math.max(110, label.implicitWidth + (iconName ? 64 : 38))
    hoverEnabled: true
    font.family: "Segoe UI"; font.pixelSize: 16
    background: Rectangle {
        radius: 7
        color: !control.enabled ? "#1b2735" : control.primary ? (control.down ? "#5136d0" : control.hovered ? "#6247ff" : Theme.violet) : control.subtle ? "transparent" : control.hovered ? "#25334a" : Theme.panel2
        border.color: control.primary ? "#866fff" : control.danger ? Theme.red : control.subtle ? "transparent" : "#50617e"
        border.width: control.subtle ? 0 : 1
        opacity: control.enabled ? 1 : 0.55
    }
    contentItem: Item {
        Row {
            spacing: 10
            anchors.centerIn: parent
            StudioIcon { name: control.iconName; width: 21; height: 21; visible: control.iconName !== "" }
            Text { id: label; text: control.text; color: control.danger ? Theme.red : Theme.text; font: control.font; verticalAlignment: Text.AlignVCenter }
        }
    }
}
