import QtQuick
import QtQuick.Controls
Button {
    id: control
    property string iconName: ""
    property bool primary: false
    property bool danger: false
    property bool subtle: false
    property int buttonHeight: Theme.controlHeight
    implicitHeight: buttonHeight
    implicitWidth: Math.max(88, label.implicitWidth + (iconName ? 48 : 28))
    hoverEnabled: true
    font.family: "Segoe UI"; font.pixelSize: 14
    background: Rectangle {
        radius: 6
        color: !control.enabled ? "#1b2735" : control.primary ? (control.down ? "#5136d0" : control.hovered ? "#6247ff" : Theme.violet) : control.subtle ? "transparent" : control.hovered ? "#25334a" : Theme.panel2
        border.color: control.primary ? "#866fff" : control.danger ? Theme.red : control.subtle ? "transparent" : "#50617e"
        border.width: control.subtle ? 0 : 1
        opacity: control.enabled ? 1 : 0.55
    }
    contentItem: Item {
        Row {
            spacing: 8
            anchors.centerIn: parent
            StudioIcon { name: control.iconName; width: 17; height: 17; visible: control.iconName !== "" }
            Text { id: label; text: control.text; color: control.danger ? Theme.red : Theme.text; font: control.font; verticalAlignment: Text.AlignVCenter }
        }
    }
}
