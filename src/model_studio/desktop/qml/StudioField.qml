import QtQuick
import QtQuick.Controls
TextField {
    id: control
    implicitHeight: Theme.controlHeight
    color: Theme.text
    placeholderTextColor: Theme.faint
    selectionColor: Theme.violet
    selectedTextColor: Theme.text
    font.family: "Segoe UI"; font.pixelSize: 14
    leftPadding: 12; rightPadding: 12
    cursorDelegate: Rectangle { width: 1; color: Theme.text }
    background: Rectangle { radius: 6; color: Theme.panel2; border.color: control.activeFocus ? Theme.violet : Theme.border; border.width: 1 }
}
