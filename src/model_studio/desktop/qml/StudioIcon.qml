import QtQuick
Image {
    property string name: "info-circle"
    property color tint: Theme.muted
    width: 22; height: 22
    source: "icons/" + name + ".svg"
    fillMode: Image.PreserveAspectFit
    smooth: true
    opacity: enabled ? 1 : 0.45
}
