import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

Item {
    id: page
    property var bridge
    property var host
    property string query: ""
    property string filter: "Все"
    readonly property var listedModels: (bridge.models || []).filter(function(m) { return page.filter==="Вспомогательные" ? m.testable===false : m.testable!==false })
    function v(o,k,d) { let x=o && o[k]; return x===undefined || x===null || x==="" ? d : x }
    function contextLabel(x) { let n=Number(x); return x===undefined || x===null || x==="" ? "Нет данных" : Number.isFinite(n) && n>=1024 ? (n%1024===0 ? n/1024 : Math.round(n/1000))+"K" : String(x) }
    function runtimeEntries() { let a=[{id:"__default__",name:"Обычный llama.cpp"}], p=v(bridge.settings,"runtime_profiles",{}); for (let id of Object.keys(p)) a.push({id:id,name:v(p[id],"name",id)}); return a }
    ColumnLayout { anchors.fill: parent; anchors.margins: Theme.pagePadding; spacing: 12
        RowLayout { Layout.fillWidth: true
            ColumnLayout { spacing: 3
                Text { text: "Модели"; color: Theme.text; font.pixelSize: 28; font.bold: true }
                Text { text: "Установленные модели и сохранённая история."; color: Theme.muted; font.pixelSize: 15 }
            }
            Item { Layout.fillWidth: true }
            StudioButton { text: "Обновить"; iconName: "refresh"; onClicked: bridge.refresh() }
            StudioButton { text: "Добавить файл…"; iconName: "plus"; primary: true; onClicked: bridge.addModelFile() }
            StudioButton { text: "Добавить папку…"; iconName: "folder"; onClicked: bridge.addModelFolder() }
        }
        RowLayout { Layout.fillWidth: true; spacing: 10
            StudioField { Layout.preferredWidth: 336; placeholderText: "Поиск моделей…"; onTextChanged: page.query=text.toLowerCase() }
            Item { Layout.fillWidth: true }
            Repeater { model: ["Установлены","Все","Архив","Вспомогательные"]
                StudioButton { required property string modelData; text: modelData; primary: page.filter===modelData; onClicked: page.filter=modelData }
            }
        }
        RowLayout { Layout.fillHeight: true; Layout.fillWidth: true; spacing: 12
            ColumnLayout { Layout.preferredWidth: Math.min(340,page.width*0.34); Layout.maximumWidth: 356; Layout.fillHeight: true; spacing: 6
                Text { text: (page.filter==="Вспомогательные" ? "Дополнительных файлов: " : "Моделей: ") + page.listedModels.length; color: Theme.text; font.pixelSize: 14; font.bold: true }
                ListView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; model: page.listedModels; spacing: 6
                    delegate: StudioCard { required property var modelData; width: ListView.view.width; height: visible ? 64 : 0
                        property bool installed: !!page.v(modelData,"available",false)
                        visible: String(page.v(modelData,"name","")).toLowerCase().indexOf(page.query)>=0 && (page.filter==="Все" || page.filter==="Вспомогательные" || (page.filter==="Установлены" && installed) || (page.filter==="Архив" && !installed))
                        color: page.v(bridge.selectedModel,"id","")===page.v(modelData,"id","") ? "#202648" : Theme.panel
                        border.color: page.v(bridge.selectedModel,"id","")===page.v(modelData,"id","") ? Theme.violet : Theme.border
                        RowLayout { anchors.fill: parent; anchors.margins: 12; spacing: 11
                            StudioIcon { name: "cube"; width: 22; height: 22 }
                            ColumnLayout { Layout.fillWidth: true; spacing: 4
                                Text { text: page.v(modelData,"name","Модель"); color: Theme.text; font.pixelSize: 15; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                                Text { text: page.v(modelData,"backend","Формат неизвестен") + "  •  " + page.v(modelData,"path",""); color: Theme.muted; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
                            }
                            Text { text: installed ? "●" : "○"; color: installed ? Theme.green : Theme.faint; font.pixelSize: 15 }
                        }
                        MouseArea { anchors.fill: parent; onClicked: bridge.selectModel(page.v(modelData,"id","")) }
                    }
                }
            }
            Rectangle { Layout.preferredWidth: 1; Layout.fillHeight: true; color: Theme.border }
            ScrollView { id: detailScroll; Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                ColumnLayout { width: Math.max(500, detailScroll.width-4); spacing: 10
                    RowLayout { Layout.fillWidth: true
                        StudioCard { Layout.preferredWidth: 64; Layout.preferredHeight: 64; StudioIcon { anchors.centerIn: parent; name: "cube"; width: 32; height: 32 } }
                        ColumnLayout { Layout.fillWidth: true; spacing: 5
                            Text { text: page.v(bridge.selectedModel,"name","Выберите модель"); color: Theme.text; font.pixelSize: 22; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                            Text { text: page.v(bridge.selectedModel,"available",false) ? page.v(bridge.selectedModel,"testable",true) ? "●  Установлена" : "●  Вспомогательный файл" : "○  Не установлена"; color: page.v(bridge.selectedModel,"available",false) ? Theme.green : Theme.muted; font.pixelSize: 13 }
                            Text { text: "Формат: " + page.v(bridge.selectedModel,"backend","Нет данных") + "  •  Runtime: " + page.v(bridge.selectedModel,"runtime_name","Нет данных"); color: Theme.muted; font.pixelSize: 13 }
                        }
                    }
                    RowLayout { visible: page.v(bridge.selectedModel,"backend","")==="gguf" && Object.keys(page.v(bridge.settings,"runtime_profiles",{})).length>0; Layout.fillWidth: true; spacing: 10
                        Text { text: "Runtime"; color: Theme.text; font.pixelSize: 13 }
                        SearchSelect { Layout.fillWidth: true; entries: page.runtimeEntries(); selectedId: page.v(bridge.selectedModel,"runtime_profile_id",page.v(page.v(bridge.settings,"model_profiles",{}),page.v(bridge.selectedModel,"path",""),"__default__")); placeholder: "Обычный llama.cpp"; iconName: "settings"; onSelected: (id) => bridge.assignRuntime(id==="__default__" ? "" : id) }
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                    Text { text: "Возможности"; color: Theme.text; font.pixelSize: 17; font.bold: true }
                    GridLayout { Layout.fillWidth: true; columns: 2; rowSpacing: 7; columnSpacing: 7
                        Repeater { model: ["MTP","Рассуждение","Работа с инструментами","Изображения"]
                            StudioCard { required property string modelData; required property int index; property bool supported: (page.v(bridge.selectedModel,"capabilities",[]) || []).indexOf(["mtp","reasoning","tools","vision"][index])>=0; Layout.fillWidth: true; Layout.preferredHeight: 59
                                MouseArea { anchors.fill: parent; enabled: index===3 && page.v(bridge.selectedModel,"backend","")==="gguf" && page.v(bridge.selectedModel,"testable",true); cursorShape: Qt.PointingHandCursor; onClicked: bridge.chooseProjector() }
                                Row { anchors.centerIn: parent; spacing: 10
                                    StudioIcon { name: supported ? "check" : "info-circle"; width: 18; height: 18 }
                                    Column { Text { text: modelData; color: Theme.text; font.pixelSize: 14 } Text { text: index===3 && page.v(bridge.selectedModel,"backend","")==="gguf" && page.v(bridge.selectedModel,"testable",true) ? (page.v(bridge.selectedModel,"mmproj_path","") ? "mmproj выбран · изменить" : "Выбрать mmproj…") : supported ? "Заявлено runtime" : "Не проверено"; color: Theme.muted; font.pixelSize: 12 } }
                                }
                            }
                        }
                    }
                    RowLayout { Layout.fillWidth: true
                        Text { text: "Рекомендации"; color: Theme.text; font.pixelSize: 17; font.bold: true; Layout.fillWidth: true }
                        StudioButton { text: "Открыть результаты"; subtle: true; onClicked: host.navigate(3) }
                    }
                    RowLayout { Layout.fillWidth: true; spacing: 7
                        Repeater { model: bridge.recommendations || []
                            StudioCard { required property var modelData; Layout.fillWidth: true; Layout.preferredHeight: 70; opacity: page.v(modelData,"available",false) ? 1 : 0.7
                                Column { anchors.centerIn: parent; spacing: 6
                                    Text { text: page.v(modelData,"title",""); color: Theme.text; font.pixelSize: 13 }
                                    Text { text: page.v(modelData,"available",false) ? page.contextLabel(page.v(modelData,"context",null))+"  •  "+page.v(modelData,"speed","Нет данных")+" ток/с" : "Нет проверенного режима"; color: Theme.muted; font.pixelSize: 13 }
                                }
                            }
                        }
                    }
                    RowLayout { Layout.fillWidth: true; spacing: 8
                        StudioButton { text: "Выбрать для запуска"; iconName: "player-play"; primary: true; Layout.fillWidth: true; enabled: !!page.v(bridge.selectedModel,"available",false) && page.v(bridge.selectedModel,"testable",true); onClicked: host.navigate(0) }
                        StudioButton { text: "Исследовать модель"; iconName: "search"; Layout.fillWidth: true; enabled: !!page.v(bridge.selectedModel,"available",false) && page.v(bridge.selectedModel,"testable",true); onClicked: host.navigate(3) }
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                    RowLayout { Layout.fillWidth: true
                        StudioButton { text: "Проверить файл"; iconName: "refresh"; enabled: !!page.v(bridge.selectedModel,"id",""); onClicked: bridge.verifyModel(page.v(bridge.selectedModel,"id","")) }
                        Item { Layout.fillWidth: true }
                        StudioButton { text: "Удалить файл модели…"; iconName: "trash"; danger: true; enabled: !!page.v(bridge.selectedModel,"available",false); onClicked: deleteDialog.open() }
                    }
                    Text { text: "История тестов и рекомендации сохранятся после удаления файла."; color: Theme.muted; font.pixelSize: 12 }
                }
            }
        }
    }
    Dialog { id: deleteDialog; modal: true; anchors.centerIn: parent; title: "Удалить файл модели?"; standardButtons: Dialog.Yes | Dialog.Cancel; onAccepted: bridge.deleteModel(page.v(bridge.selectedModel,"id",""))
        contentItem: Text { text: "Файл модели будет удалён. История тестов останется."; color: Theme.text; wrapMode: Text.WordWrap }
        background: Rectangle { color: Theme.panel2; border.color: Theme.border; radius: 6 }
    }
}
