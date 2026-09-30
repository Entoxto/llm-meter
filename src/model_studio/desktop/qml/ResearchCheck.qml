import QtQuick
import QtQuick.Controls
import "."

CheckBox {
    id: control
    implicitHeight: 28
    spacing: 9
    font.family: "Segoe UI"
    font.pixelSize: 13
    indicator: Rectangle {
        x: control.leftPadding
        y: (control.height-height)/2
        implicitWidth: 18
        implicitHeight: 18
        radius: 4
        color: control.checked ? Theme.violet : Theme.panel2
        border.color: control.checked ? "#9679ff" : Theme.border
        border.width: 1
        StudioIcon { anchors.centerIn: parent; name: "check"; visible: control.checked; width: 14; height: 14 }
    }
    contentItem: Text {
        text: control.text
        color: control.enabled ? Theme.text : Theme.faint
        font: control.font
        verticalAlignment: Text.AlignVCenter
        leftPadding: control.indicator.width + control.spacing
    }
}
