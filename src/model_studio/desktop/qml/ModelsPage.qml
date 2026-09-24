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
    function v(o,k,d) { let x=o && o[k]; return x===undefined || x===null || x==="" ? d : x }
    function contextLabel(x) { let n=Number(x); return x===undefined || x===null || x==="" ? "Нет данных" : Number.isFinite(n) && n>=1024 ? (n%1024===0 ? n/1024 : Math.round(n/1000))+"K" : String(x) }
    function runtimeEntries() { let a=[{id:"__default__",name:"Обычный llama.cpp"}], p=v(bridge.settings,"runtime_profiles",{}); for (let id of Object.keys(p)) a.push({id:id,name:v(p[id],"name",id)}); return a }
    ColumnLayout { anchors.fill: parent; anchors.margins: 40; spacing: 15
        RowLayout { Layout.fillWidth: true
            ColumnLayout { spacing: 4
                Text { text: "Модели"; color: Theme.text; font.pixelSize: 38; font.bold: true }
                Text { text: "Установленные модели и сохранённая история."; color: Theme.muted; font.pixelSize: 18 }
            }
            Item { Layout.fillWidth: true }
            StudioButton { text: "Добавить файл…"; iconName: "plus"; primary: true; onClicked: bridge.addModelFile() }
            StudioButton { text: "Добавить папку…"; iconName: "folder"; onClicked: bridge.addModelFolder() }
        }
        RowLayout { Layout.fillWidth: true; spacing: 12
            StudioField { Layout.preferredWidth: 420; placeholderText: "Поиск моделей…"; onTextChanged: page.query=text.toLowerCase() }
            Item { Layout.fillWidth: true }
            Repeater { model: ["Установлены","Все","Архив"]
                StudioButton { required property string modelData; text: modelData; primary: page.filter===modelData; onClicked: page.filter=modelData }
            }
        }
        RowLayout { Layout.fillHeight: true; Layout.fillWidth: true; spacing: 15
            ColumnLayout { Layout.preferredWidth: Math.min(445,page.width*0.38); Layout.maximumWidth: 445; Layout.fillHeight: true; spacing: 8
                Text { text: "Моделей: " + (bridge.models || []).length; color: Theme.text; font.pixelSize: 17; font.bold: true }
                ListView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; model: bridge.models || []; spacing: 8
                    delegate: StudioCard { required property var modelData; width: ListView.view.width; height: visible ? 79 : 0
                        property bool installed: !!page.v(modelData,"available",false)
                        visible: String(page.v(modelData,"name","")).toLowerCase().indexOf(page.query)>=0 && (page.filter==="Все" || (page.filter==="Установлены" && installed) || (page.filter==="Архив" && !installed))
                        color: page.v(bridge.selectedModel,"id","")===page.v(modelData,"id","") ? "#202648" : Theme.panel
                        border.color: page.v(bridge.selectedModel,"id","")===page.v(modelData,"id","") ? Theme.violet : Theme.border
                        RowLayout { anchors.fill: parent; anchors.margins: 15; spacing: 14
                            StudioIcon { name: "cube"; width: 28; height: 28 }
                            ColumnLayout { Layout.fillWidth: true; spacing: 5
                                Text { text: page.v(modelData,"name","Модель"); color: Theme.text; font.pixelSize: 18; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                                Text { text: page.v(modelData,"backend","Формат неизвестен") + "  •  " + page.v(modelData,"path",""); color: Theme.muted; font.pixelSize: 13; elide: Text.ElideRight; Layout.fillWidth: true }
                            }
                            Text { text: installed ? "●" : "○"; color: installed ? Theme.green : Theme.faint; font.pixelSize: 18 }
                        }
                        MouseArea { anchors.fill: parent; onClicked: bridge.selectModel(page.v(modelData,"id","")) }
                    }
                }
            }
            Rectangle { Layout.preferredWidth: 1; Layout.fillHeight: true; color: Theme.border }
            ScrollView { id: detailScroll; Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                ColumnLayout { width: Math.max(550, detailScroll.width-4); spacing: 16
                    RowLayout { Layout.fillWidth: true
                        StudioCard { Layout.preferredWidth: 110; Layout.preferredHeight: 110; StudioIcon { anchors.centerIn: parent; name: "cube"; width: 55; height: 55 } }
                        ColumnLayout { Layout.fillWidth: true; spacing: 6
                            Text { text: page.v(bridge.selectedModel,"name","Выберите модель"); color: Theme.text; font.pixelSize: 26; font.bold: true }
                            Text { text: page.v(bridge.selectedModel,"available",false) ? page.v(bridge.selectedModel,"testable",true) ? "●  Установлена" : "●  Вспомогательный файл" : "○  Не установлена"; color: page.v(bridge.selectedModel,"available",false) ? Theme.green : Theme.muted; font.pixelSize: 15 }
                            Text { text: "Формат: " + page.v(bridge.selectedModel,"backend","Нет данных") + "  •  Runtime: " + page.v(bridge.selectedModel,"runtime_name","Нет данных"); color: Theme.muted; font.pixelSize: 15 }
                        }
                    }
                    RowLayout { visible: page.v(bridge.selectedModel,"backend","")==="gguf" && Object.keys(page.v(bridge.settings,"runtime_profiles",{})).length>0; Layout.fillWidth: true; spacing: 13
                        Text { text: "Runtime"; color: Theme.text; font.pixelSize: 15 }
                        SearchSelect { Layout.fillWidth: true; entries: page.runtimeEntries(); selectedId: page.v(bridge.selectedModel,"runtime_profile_id",page.v(page.v(bridge.settings,"model_profiles",{}),page.v(bridge.selectedModel,"path",""),"__default__")); placeholder: "Обычный llama.cpp"; iconName: "settings"; onSelected: (id) => bridge.assignRuntime(id==="__default__" ? "" : id) }
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                    Text { text: "Возможности"; color: Theme.text; font.pixelSize: 20; font.bold: true }
                    GridLayout { Layout.fillWidth: true; columns: 2; rowSpacing: 9; columnSpacing: 9
                        Repeater { model: ["MTP","Рассуждение","Работа с инструментами","Изображения"]
                            StudioCard { required property string modelData; required property int index; property bool supported: (page.v(bridge.selectedModel,"capabilities",[]) || []).indexOf(["mtp","reasoning","tools","vision"][index])>=0; Layout.fillWidth: true; Layout.preferredHeight: 74
                                Row { anchors.centerIn: parent; spacing: 12
                                    StudioIcon { name: supported ? "check" : "info-circle"; width: 22; height: 22 }
                                    Column { Text { text: modelData; color: Theme.text; font.pixelSize: 16 } Text { text: supported ? "Заявлено runtime" : "Не проверено"; color: Theme.muted; font.pixelSize: 14 } }
                                }
                            }
                        }
                    }
                    RowLayout { Layout.fillWidth: true
                        Text { text: "Рекомендации"; color: Theme.text; font.pixelSize: 20; font.bold: true; Layout.fillWidth: true }
                        StudioButton { text: "Открыть результаты"; subtle: true; onClicked: host.navigate(3) }
                    }
                    RowLayout { Layout.fillWidth: true; spacing: 9
                        Repeater { model: bridge.recommendations || []
                            StudioCard { required property var modelData; Layout.fillWidth: true; Layout.preferredHeight: 88; opacity: page.v(modelData,"available",false) ? 1 : 0.7
                                Column { anchors.centerIn: parent; spacing: 7
                                    Text { text: page.v(modelData,"title",""); color: Theme.text; font.pixelSize: 15 }
                                    Text { text: page.v(modelData,"available",false) ? page.contextLabel(page.v(modelData,"context",null))+"  •  "+page.v(modelData,"speed","Нет данных")+" ток/с" : "Нет проверенного режима"; color: Theme.muted; font.pixelSize: 15 }
                                }
                            }
                        }
                    }
                    RowLayout { Layout.fillWidth: true; spacing: 10
                        StudioButton { text: "Выбрать для запуска"; iconName: "player-play"; primary: true; Layout.fillWidth: true; enabled: !!page.v(bridge.selectedModel,"available",false) && page.v(bridge.selectedModel,"testable",true); onClicked: host.navigate(0) }
                        StudioButton { text: "Исследовать модель"; iconName: "search"; Layout.fillWidth: true; enabled: !!page.v(bridge.selectedModel,"available",false) && page.v(bridge.selectedModel,"testable",true); onClicked: host.navigate(3) }
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                    RowLayout { Layout.fillWidth: true
                        StudioButton { text: "Проверить файл"; iconName: "refresh"; enabled: !!page.v(bridge.selectedModel,"id",""); onClicked: bridge.verifyModel(page.v(bridge.selectedModel,"id","")) }
                        Item { Layout.fillWidth: true }
                        StudioButton { text: "Удалить файл модели…"; iconName: "trash"; danger: true; enabled: !!page.v(bridge.selectedModel,"available",false); onClicked: deleteDialog.open() }
                    }
                    Text { text: "История тестов и рекомендации сохранятся после удаления файла."; color: Theme.muted; font.pixelSize: 13 }
                }
            }
        }
    }
    Dialog { id: deleteDialog; modal: true; anchors.centerIn: parent; title: "Удалить файл модели?"; standardButtons: Dialog.Yes | Dialog.Cancel; onAccepted: bridge.deleteModel(page.v(bridge.selectedModel,"id",""))
        contentItem: Text { text: "Файл модели будет удалён. История тестов останется."; color: Theme.text; wrapMode: Text.WordWrap }
        background: Rectangle { color: Theme.panel2; border.color: Theme.border; radius: 8 }
    }
}
