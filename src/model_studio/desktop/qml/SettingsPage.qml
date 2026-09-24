import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

Item {
    id: page
    property var bridge
    property var host
    function v(o,k,d) { let x=o && o[k]; return x===undefined || x===null || x==="" ? d : x }
    ScrollView { anchors.fill: parent; clip: true; ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout { x: Theme.pagePadding; width: Math.max(650,page.width-2*Theme.pagePadding); spacing: 14
            Item { Layout.preferredHeight: 2 }
            Text { text: "Настройки"; color: Theme.text; font.pixelSize: 28; font.bold: true }
            Text { text: "Настройте подключения, хранилище данных и внешние инструменты."; color: Theme.muted; font.pixelSize: 15 }
            RowLayout { Layout.fillWidth: true; spacing: 22
                ColumnLayout { Layout.fillWidth: true; Layout.alignment: Qt.AlignTop; spacing: 14
                    ColumnLayout { Layout.fillWidth: true; spacing: 6
                        Text { text: "Подключения"; color: Theme.text; font.pixelSize: 18; font.bold: true }
                        Text { text: "Локальные компоненты для работы с моделями."; color: Theme.muted; font.pixelSize: 13 }
                        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                        RowLayout { Layout.fillWidth: true
                            Text { text: "Ollama"; color: Theme.text; font.pixelSize: 15; Layout.fillWidth: true }
                            Text { text: bridge.settings.ollama_available===true ? "●  Доступен" : bridge.settings.ollama_available===false ? "●  Недоступен" : "○  Не проверен"; color: bridge.settings.ollama_available===true ? Theme.green : bridge.settings.ollama_available===false ? Theme.red : Theme.muted; font.pixelSize: 13 }
                            StudioButton { text: "Обновить"; iconName: "refresh"; onClicked: bridge.refresh() }
                        }
                        StudioField { id: ollamaHost; Layout.fillWidth: true; text: page.v(page.v(bridge.settings,"backend_hosts",{}),"Ollama","http://localhost:11434"); placeholderText: "Адрес Ollama" }
                        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                        RowLayout { Layout.fillWidth: true
                            Text { text: "llama.cpp"; color: Theme.text; font.pixelSize: 15; Layout.fillWidth: true }
                            Text { text: bridge.settings.llama_available===true ? "●  Файл найден" : bridge.settings.llama_available===false ? "●  Не найден" : "○  Не проверен"; color: bridge.settings.llama_available===true ? Theme.green : bridge.settings.llama_available===false ? Theme.red : Theme.muted; font.pixelSize: 13 }
                            StudioButton { text: "Выбрать runtime"; onClicked: bridge.chooseRuntime() }
                        }
                        StudioField { id: llamaHost; Layout.fillWidth: true; text: page.v(bridge.settings,"managed_host",page.v(page.v(bridge.settings,"backend_hosts",{}),"llama.cpp","")); placeholderText: "Адрес llama.cpp" }
                        StudioField { Layout.fillWidth: true; readOnly: true; selectByMouse: true; text: page.v(bridge.settings,"server_exe",""); placeholderText: "Путь к llama-server.exe ещё не выбран" }
                        CheckBox { id: externalLlama; text: "Подключить внешний llama.cpp"; checked: !!page.v(bridge.settings,"enable_external_llama",false) }
                        StudioField { id: externalHost; Layout.fillWidth: true; enabled: externalLlama.checked; text: page.v(bridge.settings,"external_host",""); placeholderText: "Адрес внешнего сервера llama.cpp" }
                    }
                    ColumnLayout { Layout.fillWidth: true; spacing: 8
                        Text { text: "Папки моделей"; color: Theme.text; font.pixelSize: 18; font.bold: true }
                        Text { text: "Здесь хранятся загруженные модели и связанные файлы."; color: Theme.muted; font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        ListView { Layout.fillWidth: true; Layout.preferredHeight: Math.max(40,Math.min(120,(page.v(bridge.settings,"model_dirs",[]) || []).length*40)); model: page.v(bridge.settings,"model_dirs",[]); clip: true
                            delegate: StudioCard { required property var modelData; width: ListView.view.width; height: 38
                                Row { anchors.verticalCenter: parent.verticalCenter; anchors.left: parent.left; anchors.leftMargin: 13; spacing: 11
                                    StudioIcon { name: "folder" }
                                    Text { text: String(modelData); color: Theme.text; font.pixelSize: 14; elide: Text.ElideMiddle; width: Math.max(100,page.width/3) }
                                }
                            }
                        }
                        StudioButton { text: "Добавить папку…"; iconName: "folder"; onClicked: bridge.addModelFolder() }
                    }
                    StudioButton { text: "Импорт прежних данных"; iconName: "download"; onClicked: bridge.importLegacy() }
                }
                Rectangle { Layout.preferredWidth: 1; Layout.fillHeight: true; color: Theme.border }
                ColumnLayout { Layout.fillWidth: true; Layout.alignment: Qt.AlignTop; spacing: 15
                    ColumnLayout { Layout.fillWidth: true; spacing: 8
                        Text { text: "OpenCode"; color: Theme.text; font.pixelSize: 18; font.bold: true }
                        Text { text: "Интеграция с внешним редактором для работы с моделями."; color: Theme.muted; font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        StudioField { id: opencodePath; Layout.fillWidth: true; text: page.v(bridge.settings,"opencode_exe",""); placeholderText: "Путь к приложению OpenCode" }
                        StudioButton { text: "Выбрать приложение…"; iconName: "folder"; onClicked: bridge.chooseOpenCode() }
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                    ColumnLayout { Layout.fillWidth: true; spacing: 10
                        Text { text: "Данные и история"; color: Theme.text; font.pixelSize: 18; font.bold: true }
                        Text { text: "История тестов и чатов хранится локально. Отчёты создаются при экспорте."; color: Theme.muted; font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        Row { spacing: 10; StudioIcon { name: "database" } Text { text: "История тестов сохраняется автоматически"; color: Theme.text; font.pixelSize: 14 } }
                        Row { spacing: 10; StudioIcon { name: "message" } Text { text: "История чатов хранится локально"; color: Theme.text; font.pixelSize: 14 } }
                        RowLayout { Layout.fillWidth: true
                            StudioButton { text: "Резервная копия…"; iconName: "database"; Layout.fillWidth: true; onClicked: bridge.backup() }
                            StudioButton { text: "Восстановить…"; iconName: "refresh"; Layout.fillWidth: true; onClicked: restoreDialog.open() }
                        }
                        StudioButton { text: "Открыть папку отчётов"; iconName: "folder"; Layout.fillWidth: true; onClicked: bridge.openReports() }
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                    ColumnLayout { Layout.fillWidth: true; spacing: 7
                        Text { text: "Окружение для рекомендаций"; color: Theme.text; font.pixelSize: 18; font.bold: true }
                        Text { text: "Текущий компьютер  •  " + page.v(bridge.settings,"environment","Нет данных"); color: Theme.text; font.pixelSize: 14; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        Text { text: "После обновления runtime режимы могут потребовать новой проверки."; color: Theme.muted; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    }
                }
            }
            RowLayout { Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                StudioButton { text: "Сохранить настройки"; iconName: "check"; primary: true; onClicked: bridge.saveSettings({backend_hosts:{"Ollama":ollamaHost.text.trim(),"llama.cpp":llamaHost.text.trim()},managed_host:llamaHost.text.trim(),opencode_exe:opencodePath.text.trim(),enable_external_llama:externalLlama.checked,external_host:externalHost.text.trim()}) }
            }
            Item { Layout.preferredHeight: 14 }
        }
    }
    Dialog { id: restoreDialog; title: "Восстановить данные?"; modal: true; anchors.centerIn: parent; standardButtons: Dialog.Yes | Dialog.Cancel; onAccepted: bridge.restoreBackup()
        contentItem: Text { text: "Текущие локальные данные будут заменены резервной копией."; color: Theme.text; wrapMode: Text.WordWrap }
        background: Rectangle { color: Theme.panel2; border.color: Theme.border; radius: 6 }
    }
}
