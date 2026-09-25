import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

Item {
    id: page
    objectName: "launchPage"
    property var bridge
    property var host
    property bool manual: false
    property string selectedMode: {
        let id=v(bridge ? bridge.matchingResult : {},"id",""); if (!id) return ""
        let a=bridge ? (bridge.recommendations || []) : []
        for (let key of ["balanced","speed","context"]) for (let rec of a) if (rec.key===key && rec.available && rec.result_id===id) return key
        return ""
    }
    property int customContext: 100000
    function v(o,k,d) { let x=o && o[k]; return x===undefined || x===null || x==="" ? d : x }
    function shown(x,s) { return x===undefined || x===null || x==="" ? "—" : (typeof x==="number" && Number.isFinite(x) ? x.toLocaleString(Qt.locale("ru_RU"), "f", s===" с" ? 3 : 2) : String(x))+(s||"") }
    function contextLabel(x) { let n=Number(x); return x===undefined || x===null || x==="" ? "—" : Number.isFinite(n) && n>=1024 ? (n%1024===0 ? n/1024 : Math.round(n/1000))+"K" : String(x) }
    function supports(cap) { return (v(bridge.selectedModel,"capabilities",[]) || []).indexOf(cap)>=0 }
    readonly property var reasoningOptions: [
        {text:"Off",mode:"off",budget:0}, {text:"2K",mode:"on",budget:2048},
        {text:"4K",mode:"on",budget:4096}, {text:"8K",mode:"on",budget:8192},
        {text:"Auto",mode:"auto",budget:0}, {text:"On · без лимита",mode:"on",budget:0}]
    function reasoningSupported(option) {
        return option.mode==="auto" || (supports("reasoning") && (!option.budget ||
            (supports("reasoning-budget") && v(bridge.selectedModel,"backend","")==="gguf")))
    }
    function reasoningIndex() {
        let budget=Number(v(bridge.draft,"reasoning_budget",0)), mode=v(bridge.draft,"reasoning","auto")
        return Math.max(0,reasoningOptions.findIndex(function(o) { return o.mode===mode && o.budget===budget }))
    }
    function startable() { return !!v(bridge.selectedModel,"id","") && !!v(bridge.selectedModel,"available",false) && v(bridge.selectedModel,"testable",true) }
    ScrollView { anchors.fill: parent; clip: true; ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout {
            width: Math.max(650, page.width-2*Theme.pagePadding); x: Theme.pagePadding; spacing: 15
            Item { Layout.preferredHeight: 4 }
            Text { text: manual ? "Ручная настройка" : "Запуск модели"; color: Theme.text; font.pixelSize: 28; font.bold: true; font.family: "Segoe UI" }
            Text { text: manual ? "Настройте параметры модели под свою задачу. Изменения применятся при запуске." : "Выберите модель, режим работы и запустите локальный сервер."; color: Theme.muted; font.pixelSize: 15; font.family: "Segoe UI"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            RowLayout { Layout.fillWidth: true; spacing: 19
                ColumnLayout { Layout.fillWidth: true; spacing: 4
                    Text { text: "Модель · доступно: " + (bridge.models || []).filter(function(e) { return e.available && e.testable!==false }).length; color: Theme.text; font.pixelSize: 14 }
                    SearchSelect { objectName: "modelSelect"; Layout.fillWidth: true; entries: (bridge.models || []).filter(function(e) { return e.available && e.testable!==false }); selectedId: page.v(bridge.selectedModel,"id",""); placeholder: "Выберите модель"; iconName: "cube"; onSelected: (id) => { page.selectedMode=""; bridge.selectModel(id) } }
                }
                Rectangle { Layout.preferredWidth: 1; Layout.preferredHeight: 54; color: Theme.border }
                Column { Layout.preferredWidth: 176; spacing: 7
                    Text { text: "Среда выполнения"; color: Theme.text; font.pixelSize: 14 }
                    Text { text: page.v(bridge.selectedModel,"runtime_name",page.v(bridge.draft,"runtime_name","Нет данных")); color: Theme.muted; font.pixelSize: 14 }
                }
                Rectangle { Layout.preferredWidth: 1; Layout.preferredHeight: 54; color: Theme.border }
                Column { Layout.preferredWidth: 176; spacing: 7
                    Text { text: "Статус"; color: Theme.text; font.pixelSize: 14 }
                    Text { text: "●  " + host.statusText(); color: host.statusColor(); font.pixelSize: 14 }
                    Text { visible: host.running(); text: page.v(bridge.session,"host",""); color: Theme.muted; font.pixelSize: 12 }
                }
            }
            Text { visible: !!page.v(bridge.session,"error",""); text: page.v(bridge.session,"error",""); color: Theme.muted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            ColumnLayout { visible: !page.manual; Layout.fillWidth: true; spacing: 8
                RowLayout { Layout.fillWidth: true
                    Text { text: "Режим работы"; color: Theme.text; font.pixelSize: 18; font.bold: true; Layout.fillWidth: true }
                    Text { text: "Лучшие из проверенных режимов"; color: Theme.muted; font.pixelSize: 12 }
                }
                RowLayout { Layout.fillWidth: true; spacing: 10
                    Repeater { model: ["speed","balanced","context"]
                        delegate: StudioCard {
                            required property string modelData
                            required property int index
                            property var rec: {
                                let a=bridge.recommendations || []
                                for(let i=0;i<a.length;i++) if(a[i].key===modelData) return a[i]
                                return null
                            }
                            Layout.fillWidth: true; Layout.preferredHeight: 139
                            color: page.selectedMode===modelData ? "#202648" : Theme.panel
                            border.color: page.selectedMode===modelData ? Theme.violet : Theme.border
                            opacity: rec && rec.available ? 1 : 0.78
                            Column { anchors.fill: parent; anchors.margins: 16; spacing: 10
                                Row { spacing: 9
                                    Rectangle { width: 18; height: 18; radius: 9; color: "transparent"; border.color: page.selectedMode===modelData ? Theme.violet : Theme.muted; border.width: 2
                                        Rectangle { anchors.centerIn: parent; width: 8; height: 8; radius: 4; color: Theme.violet; visible: page.selectedMode===modelData }
                                    }
                                    Text { text: rec ? page.v(rec,"title",["Максимальная скорость","Сбалансированный","Максимальный контекст"][index]) : ["Максимальная скорость","Сбалансированный","Максимальный контекст"][index]; color: Theme.text; font.pixelSize: 14; font.bold: true }
                                }
                                Row { spacing: 22
                                    Column { spacing: 5; Text { text: "Контекст"; color: Theme.muted; font.pixelSize: 12 } Text { text: page.contextLabel(rec && rec.available ? rec.context : null); color: Theme.text; font.pixelSize: 22; font.bold: true } }
                                    Column { spacing: 5; Text { text: "Скорость генерации"; color: Theme.muted; font.pixelSize: 12 } Text { text: page.shown(rec && rec.available ? rec.speed : null," ток/с"); color: Theme.text; font.pixelSize: 22; font.bold: true } }
                                }
                                Text { text: rec && rec.available ? "По сохранённым тестам" : page.v(rec,"reason","Выберите модель и дождитесь проверки результатов."); color: Theme.muted; font.pixelSize: 12; wrapMode: Text.WordWrap; width: parent.width; maximumLineCount: 2; elide: Text.ElideRight
                                    HoverHandler { id: recommendationReasonHover }
                                    ToolTip.visible: recommendationReasonHover.hovered && truncated
                                    ToolTip.text: text
                                }
                            }
                            MouseArea { anchors.fill: parent; enabled: !!rec && !!rec.available; onClicked: { page.selectedMode=modelData; bridge.applyRecommendation(modelData) } }
                        }
                    }
                }
            }
            RowLayout { visible: page.manual; Layout.fillWidth: true; Layout.preferredHeight: Math.max(manualContent.implicitHeight+32,450); spacing: 13
            StudioCard { Layout.fillWidth: true; Layout.preferredWidth: 616; Layout.fillHeight: true
                ColumnLayout { id: manualContent; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 16; spacing: 16
                    Text { text: "Основные параметры"; color: Theme.text; font.pixelSize: 18; font.bold: true }
                    Text { text: "Контекст"; color: Theme.text; font.pixelSize: 14 }
                    Flow { Layout.fillWidth: true; spacing: 8
                        Repeater { model: [32768,65536,98304,100000,131072]
                            StudioButton { required property int modelData; text: page.contextLabel(modelData); primary: Number(page.v(bridge.draft,"context",0))===modelData; onClicked: bridge.setDraft("context",modelData) }
                        }
                        StudioButton { text: "Свой"; onClicked: contextDialog.open() }
                    }
                    RowLayout { visible: page.v(bridge.selectedModel,"backend","")==="gguf"; Layout.fillWidth: true; spacing: 12
                        Text { text: "Изображения"; color: Theme.text; font.pixelSize: 14; Layout.preferredWidth: 136 }
                        Switch { objectName: "visionSwitch"; checked: !!page.v(bridge.draft,"vision",false); enabled: page.startable(); onToggled: {
                            if (checked && !page.v(bridge.selectedModel,"mmproj_available",false)) {
                                bridge.chooseProjector()
                                checked=Qt.binding(function() { return !!page.v(bridge.draft,"vision",false) })
                            } else bridge.setDraft("vision",checked)
                        } }
                        Text { text: page.v(bridge.selectedModel,"mmproj_available",false) ? (page.v(bridge.draft,"vision",false) ? "mmproj будет подключён" : "Только текст") : "Нужен совместимый mmproj"; color: Theme.muted; font.pixelSize: 12; Layout.fillWidth: true }
                        StudioButton { text: page.v(bridge.selectedModel,"mmproj_path","") ? "Изменить…" : "Выбрать…"; iconName: "folder"; onClicked: bridge.chooseProjector() }
                    }
                    RowLayout { Layout.fillWidth: true; spacing: 16
                        Text { text: "Рассуждение"; color: Theme.text; font.pixelSize: 14; Layout.preferredWidth: 136 }
                        ComboBox { id: reasoningSelect; objectName: "reasoningSelect"; model: page.reasoningOptions; textRole: "text"; currentIndex: page.reasoningIndex(); Layout.preferredWidth: 176
                            delegate: ItemDelegate { required property var modelData; required property int index
                                objectName: "reasoningOption"+index; width: reasoningSelect.width; text: modelData.text
                                enabled: page.reasoningSupported(modelData); opacity: enabled ? 1 : 0.4
                                highlighted: reasoningSelect.highlightedIndex===index
                            }
                            onActivated: function(index) {
                                let option=page.reasoningOptions[index]
                                if (page.reasoningSupported(option)) bridge.setReasoning(option.mode,option.budget)
                                currentIndex=Qt.binding(function() { return page.reasoningIndex() })
                            }
                        }
                        Text { text: page.supports("reasoning-budget") ? "Бюджет в токенах · Auto — выбор модели" : page.supports("reasoning") ? "Числовой бюджет не поддерживается" : "Доступен только Auto"; color: Theme.muted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    }
                    RowLayout { Layout.fillWidth: true; spacing: 16
                        Text { text: "Ускорение MTP"; color: Theme.text; font.pixelSize: 14; Layout.preferredWidth: 136 }
                        Switch { checked: !!page.v(bridge.draft,"mtp",false); enabled: page.supports("mtp"); onToggled: bridge.setDraft("mtp",checked) }
                        Text { text: page.supports("mtp") ? "Подтверждено runtime" : "Не подтверждено для этой модели/runtime"; color: Theme.muted; font.pixelSize: 12 }
                    }
                    RowLayout { Layout.fillWidth: true; spacing: 16
                        Text { text: "Draft (для MTP)"; color: Theme.text; font.pixelSize: 14; Layout.preferredWidth: 136 }
                        SpinBox { from: 1; to: 16; value: Number(page.v(bridge.draft,"draft",2)); enabled: page.supports("mtp") && !!page.v(bridge.draft,"mtp",false); onValueModified: bridge.setDraft("draft",value); Layout.preferredWidth: 104 }
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                    RowLayout { Layout.fillWidth: true
                        Text { text: "Дополнительно"; color: Theme.text; font.pixelSize: 15; font.bold: true; Layout.fillWidth: true }
                        StudioButton { text: advanced.visible ? "Свернуть" : "Развернуть"; iconName: "chevron-down"; subtle: true; onClicked: advanced.visible=!advanced.visible }
                    }
                    ColumnLayout { id: advanced; visible: false; Layout.fillWidth: true; spacing: 10
                        RowLayout { Text { text: "Память контекста"; color: Theme.text; Layout.preferredWidth: 144 } ComboBox { objectName: "kvCacheSelect"; model: page.supports("kv-cache") ? ["f16","q8_0","q4_0"] : ["f16"]; currentIndex: Math.max(0,model.indexOf(page.v(bridge.draft,"kv_type","f16"))); onActivated: bridge.setDraft("kv_type",currentText) } Text { text: page.supports("kv-cache") ? "Поддерживается сервером" : "Экономный KV не подтверждён"; color: Theme.muted } }
                        RowLayout { Text { text: "Слои на GPU"; color: Theme.text; Layout.preferredWidth: 144 } SpinBox { from: 0; to: 999; value: Number(page.v(bridge.draft,"gpu_layers",0)); onValueModified: bridge.setDraft("gpu_layers",value) } }
                    }
                }
            }
            StudioCard { Layout.preferredWidth: Math.min(280,page.width*0.30); Layout.fillHeight: true
                ColumnLayout { anchors.fill: parent; anchors.margins: 16; spacing: 10
                    Text { text: "Результаты для этих настроек"; color: Theme.text; font.pixelSize: 17; font.bold: true; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    StudioCard { Layout.fillWidth: true; Layout.preferredHeight: 168; color: Theme.panel2
                        ColumnLayout { anchors.fill: parent; anchors.margins: 14; spacing: 10
                            StudioIcon { name: page.v(bridge.matchingResult,"id","") ? "check" : "info-circle"; width: 26; height: 26; Layout.alignment: Qt.AlignHCenter }
                            Text { text: page.v(bridge.matchingResult,"id","") ? "Подходящий замер сохранён" : "Эта конфигурация ещё не тестировалась"; color: Theme.text; font.pixelSize: 14; font.bold: true; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; Layout.fillWidth: true }
                            Text { text: page.v(bridge.matchingResult,"id","") ? page.shown(page.v(bridge.matchingResult,"speed",null)," ток/с") : "Запустите тест, чтобы увидеть скорость на этом компьютере."; color: Theme.muted; font.pixelSize: 13; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; Layout.fillWidth: true }
                        }
                    }
                    StudioButton { text: "Протестировать"; iconName: "player-play"; Layout.fillWidth: true; enabled: !bridge.busy && page.startable(); onClicked: bridge.runBenchmark() }
                    Item { Layout.fillHeight: true }
                    StudioButton { text: host.running() ? "Применить настройки" : "Запустить модель"; iconName: "check"; primary: true; Layout.fillWidth: true; enabled: !bridge.busy && page.startable(); onClicked: bridge.startModel() }
                    StudioButton { text: "К рекомендованным режимам"; iconName: "arrow-left"; Layout.fillWidth: true; onClicked: page.manual=false }
                }
            }
            }
            RowLayout { visible: !page.manual; Layout.fillWidth: true; spacing: 12
                StudioButton { text: page.manual ? "К рекомендованным режимам" : "Настроить вручную"; iconName: page.manual ? "arrow-left" : "adjustments"; subtle: true; onClicked: page.manual=!page.manual }
                Item { Layout.fillWidth: true }
                StudioButton { text: host.running() ? "Применить настройки" : "Запустить модель"; iconName: "player-play"; primary: true; Layout.preferredWidth: 232; enabled: page.startable() && !bridge.busy; onClicked: bridge.startModel() }
                StudioButton { text: "Выгрузить"; iconName: "download"; Layout.preferredWidth: 192; enabled: host.running(); onClicked: unloadDialog.open() }
            }
            RowLayout { visible: !page.manual; Layout.fillWidth: true; Layout.topMargin: 10; spacing: 12
                StudioCard { Layout.fillWidth: true; Layout.preferredHeight: 280
                    ColumnLayout { anchors.fill: parent; anchors.margins: 16; spacing: 10
                        Text { text: host.running() ? "Сейчас" : "Проверено на этом компьютере"; color: Theme.text; font.pixelSize: 18; font.bold: true }
                        Text { text: host.running() ? "Текущие показатели модели и компьютера." : "Результаты подходящего сохранённого теста."; color: Theme.muted; font.pixelSize: 13 }
                        StudioCard { Layout.fillWidth: true; Layout.fillHeight: true; color: Theme.panel2
                            ColumnLayout { anchors.fill: parent; anchors.margins: 15; spacing: 8
                                Text { text: host.running() ? host.modelName() : page.v(bridge.matchingResult,"model_name",page.v(bridge.selectedModel,"name","Нет данных")); color: Theme.text; font.pixelSize: 15; font.bold: true }
                                RowLayout { Layout.fillWidth: true; spacing: 14
                                    Column { spacing: 4; Text { text: host.running() ? "Видеопамять всей GPU" : "Скорость генерации"; color: Theme.muted } Text { text: host.running() ? page.shown(page.v(bridge.telemetry,"gpu_used_gb",null)," ГБ") + " / " + page.shown(page.v(bridge.telemetry,"gpu_total_gb",null)," ГБ") : page.shown(page.v(bridge.matchingResult,"speed",null)," ток/с"); color: Theme.text; font.pixelSize: 20; font.bold: true } }
                                    Column { spacing: 4; Text { text: host.running() ? "Загрузка GPU" : "Первый токен"; color: Theme.muted } Text { text: host.running() ? page.shown(page.v(bridge.telemetry,"gpu_utilization",null),"%") : page.shown(page.v(bridge.matchingResult,"ttft",null)," с"); color: Theme.text; font.pixelSize: 20; font.bold: true } }
                                }
                                RowLayout { visible: !host.running() && !!page.v(bridge.matchingResult,"id",""); Layout.fillWidth: true; spacing: 14
                                    Column { spacing: 4; Text { text: "Обработка входа"; color: Theme.muted; font.pixelSize: 12 } Text { text: page.shown(page.v(bridge.matchingResult,"prompt_speed",null)," ток/с"); color: Theme.text; font.pixelSize: 17; font.bold: true } }
                                    Column { spacing: 4; Text { text: "Пик VRAM видеокарты"; color: Theme.muted; font.pixelSize: 12 } Text { text: page.shown(page.v(bridge.matchingResult,"vram_gb",null)," ГБ"); color: Theme.text; font.pixelSize: 17; font.bold: true } }
                                }
                                RowLayout { visible: host.running(); Layout.fillWidth: true; spacing: 14
                                    Column { spacing: 4
                                        Text { text: "RAM системы"; color: Theme.muted; font.pixelSize: 12 }
                                        Text { text: page.shown(page.v(bridge.telemetry,"ram_used_gb",null)," ГБ") + " / " + page.shown(page.v(bridge.telemetry,"ram_total_gb",null)," ГБ"); color: Theme.text; font.pixelSize: 14; font.bold: true }
                                    }
                                    Column { spacing: 4
                                        Text { text: "Буферы модели"; color: Theme.muted; font.pixelSize: 12 }
                                        Text { text: "GPU " + page.shown(page.v(bridge.telemetry,"model_vram_gb",null)," ГБ") + "  •  RAM " + page.shown(page.v(bridge.telemetry,"model_ram_gb",null)," ГБ"); color: Theme.text; font.pixelSize: 14; font.bold: true }
                                    }
                                }
                                Text { visible: host.running(); text: "Offload: " + page.shown(page.v(bridge.telemetry,"offload",null)) + "  •  Источник: " + page.shown(page.v(bridge.telemetry,"model_memory_source",null)); color: Theme.muted; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                Text { text: host.running() ? "Историческая скорость доступна в результатах исследования." : page.v(bridge.matchingResult,"id","") ? "Подходящий сохранённый тест. Подробности доступны в истории." : "Эта конфигурация ещё не тестировалась."; color: Theme.muted; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            }
                        }
                    }
                }
                StudioCard { Layout.fillWidth: true; Layout.preferredHeight: 280
                    ColumnLayout { anchors.fill: parent; anchors.margins: 16; spacing: 10
                        Text { text: "Работа с моделью"; color: Theme.text; font.pixelSize: 18; font.bold: true }
                        Text { text: "После запуска откройте нужный инструмент."; color: Theme.muted; font.pixelSize: 13 }
                        StudioButton { text: "Открыть чат"; iconName: "message"; Layout.fillWidth: true; enabled: host.running(); onClicked: host.navigate(1) }
                        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                        Text { text: "OpenCode"; color: Theme.text; font.pixelSize: 17; font.bold: true }
                        RowLayout { Layout.fillWidth: true; spacing: 8
                            SearchSelect { Layout.fillWidth: true; entries: bridge.projects || []; selectedId: page.v(bridge.selectedProject,"id",""); placeholder: "Выберите проект"; iconName: "search"; onSelected: (id) => bridge.selectProject(id) }
                            StudioButton { text: "Выбрать папку…"; iconName: "folder"; onClicked: bridge.openProjectFolder() }
                        }
                        StudioButton { text: "Открыть OpenCode"; iconName: "external-link"; Layout.fillWidth: true; enabled: host.running() && !!page.v(bridge.selectedProject,"id",""); onClicked: bridge.openOpenCode() }
                    }
                }
            }
            Item { Layout.preferredHeight: 13 }
        }
    }
    Dialog { id: unloadDialog; title: "Выгрузить модель?"; modal: true; standardButtons: Dialog.Yes | Dialog.Cancel; anchors.centerIn: parent; onAccepted: bridge.unloadModel(true)
        contentItem: Text { text: "Текущая работа с моделью прервётся."; color: Theme.text; wrapMode: Text.WordWrap }
        background: Rectangle { color: Theme.panel2; border.color: Theme.border; radius: 6 }
    }
    Dialog { id: contextDialog; title: "Свой контекст"; modal: true; standardButtons: Dialog.Ok | Dialog.Cancel; anchors.centerIn: parent; onAccepted: bridge.setDraft("context",customContext)
        contentItem: SpinBox { from: 1024; to: 1048576; value: page.customContext; editable: true; onValueModified: page.customContext=value }
        background: Rectangle { color: Theme.panel2; border.color: Theme.border; radius: 6 }
    }
}
