import QtQuick
Image {
    property string name: "info-circle"
    property color tint: Theme.muted
    width: 18; height: 18
    source: "icons/" + name + ".svg"
    fillMode: Image.PreserveAspectFit
    smooth: true
    opacity: enabled ? 1 : 0.45
}
