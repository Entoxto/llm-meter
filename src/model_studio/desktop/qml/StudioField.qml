import QtQuick
import QtQuick.Controls
TextField {
    id: control
    implicitHeight: 46
    color: Theme.text
    placeholderTextColor: Theme.faint
    selectionColor: Theme.violet
    selectedTextColor: Theme.text
    font.family: "Segoe UI"; font.pixelSize: 16
    leftPadding: 15; rightPadding: 15
    cursorDelegate: Rectangle { width: 1; color: Theme.text }
    background: Rectangle { radius: 7; color: Theme.panel2; border.color: control.activeFocus ? Theme.violet : Theme.border; border.width: 1 }
}
