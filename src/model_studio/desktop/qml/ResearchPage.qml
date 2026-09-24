import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

Item {
    id: page
    objectName: "researchPage"
    property var bridge
    property var host
    property string mode: "quick"
    property int maxContext: 131072
    property int budgetMinutes: 30
    property bool acknowledge: false
    property string contextFilter: ""
    property string statusFilter: ""
    property string backendFilter: ""
    property string researchFilter: ""
    function showResearch(job) {
        researchFilter=job.id; contextFilter=""; statusFilter=""; backendFilter=""
        bridge.showResearch(job.id)
    }
    readonly property var filteredResults: (researchFilter ? (bridge.researchResults || []) : (bridge.results || [])).filter(function(r) {
        return (!!page.researchFilter || !page.v(bridge.selectedModel,"id","") || page.v(r,"display_model_id",page.v(r,"model_id",""))===page.v(bridge.selectedModel,"id",""))
            && (!page.contextFilter || String(page.v(r,"context",""))===page.contextFilter)
            && (!page.statusFilter || page.v(r,"status","")===page.statusFilter)
            && (!page.backendFilter || page.v(page.v(r,"config",{}),"backend",page.v(r,"backend",""))===page.backendFilter)
    })
    function selectVisibleResult() {
        if (page.mode!=="history") return
        let current=page.v(bridge.selectedResult,"id","")
        if (page.filteredResults.some(function(r) { return r.id===current })) return
        let next=page.filteredResults.length ? page.filteredResults[0].id : ""
        if (next!==current) bridge.selectResult(next)
    }
    onFilteredResultsChanged: selectVisibleResult()
    onModeChanged: selectVisibleResult()
    function v(o,k,d) { let x=o && o[k]; return x===undefined || x===null || x==="" ? d : x }
    function shown(x,s) { if(x===undefined || x===null || x==="") return "Нет данных"; return (typeof x==="number" && Number.isFinite(x) ? x.toLocaleString(Qt.locale("ru_RU"), "f", s===" с" ? 3 : 2) : String(x))+(s||"") }
    function contextLabel(x) { let n=Number(x); return x===undefined || x===null || x==="" ? "Нет данных" : Number.isFinite(n) && n>=1024 ? (n%1024===0 ? n/1024 : Math.round(n/1000))+"K" : String(x) }
    function statusLabel(s) { return ({completed:"Сохранён", running:"Выполняется", cancelled:"Остановлен", stopped:"Прерван", interrupted:"Прерван", error:"Ошибка", failed:"Ошибка"})[s] || String(s || "Нет данных") }
    function jobReason(job) { return job.error || job.restore_error || ({budget_exhausted:"Лимит времени исчерпан", cancelled:"Остановлено пользователем", completed:"План завершён", exhausted:"План завершён", lease_or_storage_error:"Не удалось выполнить задание", configuration_limit:"Достигнут лимит конфигураций", start_failed:"Не удалось запустить модель", fit_failure:"Конфигурация не поместилась в память", measurement_failed:"Замер завершился ошибкой", error:"Ошибка выполнения"})[job.stop_reason] || job.stop_reason || "" }
    function contextOptions() { let a=[{text:"Все контексты",value:""}], seen={}; for (let r of (page.researchFilter ? (bridge.researchResults || []) : (bridge.results || []))) { let c=String(v(r,"context","")); if (c && !seen[c]) { seen[c]=true; a.push({text:contextLabel(c),value:c}) } } return a }
    readonly property bool progressing: (bridge.busy && mode==="progress") || ["running","starting","cancelling"].indexOf(v(bridge.research,"status",""))>=0
    Connections { target: bridge; function onChanged() { if (page.mode==="progress" && !bridge.busy) page.mode="history" } }
    ScrollView { anchors.fill: parent; clip: true; ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout { x: Theme.pagePadding; width: Math.max(650,page.width-2*Theme.pagePadding); spacing: 13
            Item { Layout.preferredHeight: 3 }
            RowLayout { Layout.fillWidth: true
                ColumnLayout { spacing: 3
                    Text { text: page.progressing ? "Исследуем модель" : page.mode==="history" ? "Результаты исследования" : "Исследование"; color: Theme.text; font.pixelSize: 28; font.bold: true }
                    Text { text: page.progressing ? "Автоматически проверяем конфигурации модели." : page.mode==="history" ? "Просматривайте сохранённые замеры и применяйте лучшие настройки." : "Проверяйте производительность моделей в разных режимах работы."; color: Theme.muted; font.pixelSize: 14 }
                }
                Item { Layout.fillWidth: true }
                StudioButton { text: page.progressing ? "Остановить исследование" : page.mode==="history" ? "Открыть папку отчётов" : "История результатов"; iconName: page.progressing ? "x" : "folder"; danger: page.progressing; onClicked: page.progressing ? stopDialog.open() : page.mode==="history" ? bridge.openReports() : page.mode="history" }
            }
            StudioCard { Layout.fillWidth: true; Layout.preferredHeight: 54
                RowLayout { anchors.fill: parent; anchors.margins: 13; spacing: 12
                    StudioIcon { name: "cube" }
                    Text { text: page.v(bridge.selectedModel,"name","Модель не выбрана") + "  •  " + page.v(bridge.selectedModel,"runtime_name","Среда не определена"); color: Theme.text; font.pixelSize: 14; Layout.fillWidth: true }
                    Text { text: "●  " + host.statusText(); color: host.statusColor(); font.pixelSize: 13 }
                    StudioButton { text: "Изменить параметры"; iconName: "settings"; subtle: true; onClicked: host.navigate(0) }
                }
            }
            RowLayout { visible: !page.progressing && page.mode!=="history"; spacing: 0
                StudioButton { text: "Быстрый тест"; iconName: "chart-bar"; primary: page.mode==="quick"; onClicked: page.mode="quick" }
                StudioButton { text: "Подобрать режимы"; iconName: "adjustments"; primary: page.mode==="setup"; onClicked: page.mode="setup" }
            }
            RowLayout { visible: page.mode==="quick" && !page.progressing; Layout.fillWidth: true; Layout.preferredHeight: 448; spacing: 11
                StudioCard { Layout.fillWidth: true; Layout.fillHeight: true
                    ColumnLayout { anchors.fill: parent; anchors.margins: 16; spacing: 13
                        Text { text: page.v(bridge.matchingResult,"id","") ? "Сохранённый тест текущей конфигурации" : "Быстрый тест"; color: Theme.text; font.pixelSize: 22; font.bold: true }
                        Text { text: "Проверяет скорость текущей конфигурации. Результат сохраняется автоматически."; color: Theme.muted; font.pixelSize: 14; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        RowLayout { Layout.fillWidth: true; spacing: 10
                            Repeater { model: ["Скорость генерации","Обработка входа","Первый токен"]
                                StudioCard { required property string modelData; required property int index; Layout.fillWidth: true; Layout.preferredHeight: 91; color: Theme.panel2
                                    Column { anchors.fill: parent; anchors.margins: 13; spacing: 8
                                        Text { text: modelData; color: Theme.muted; font.pixelSize: 13 }
                                        Text { text: page.shown(page.v(bridge.matchingResult,["speed","prompt_speed","ttft"][index],null),index===2 ? " с" : " ток/с"); color: Theme.text; font.pixelSize: 25; font.bold: true }
                                    }
                                }
                            }
                        }
                        StudioCard { Layout.fillWidth: true; Layout.preferredHeight: 76; color: Theme.panel2
                            Column { anchors.fill: parent; anchors.margins: 12; spacing: 6
                                Text { text: "Условия замера"; color: Theme.text; font.pixelSize: 14; font.bold: true }
                                Text { text: "Контекст: " + page.contextLabel(page.v(bridge.matchingResult,"context",null)) + "  •  VRAM: " + page.shown(page.v(bridge.matchingResult,"vram_gb",null)," ГБ"); color: Theme.muted; font.pixelSize: 13 }
                            }
                        }
                        Item { Layout.fillHeight: true }
                        StudioButton { text: page.v(bridge.matchingResult,"id","") ? "Повторить тест" : "Начать быстрый тест"; iconName: "player-play"; primary: true; Layout.fillWidth: true; enabled: !!page.v(bridge.selectedModel,"id","") && page.v(bridge.selectedModel,"testable",true) && !bridge.busy; onClicked: bridge.runBenchmark() }
                    }
                }
                StudioCard { Layout.preferredWidth: 280; Layout.fillHeight: true
                    ColumnLayout { anchors.fill: parent; anchors.margins: 16; spacing: 15
                        Text { text: "Условия теста"; color: Theme.text; font.pixelSize: 18; font.bold: true }
                        Text { text: "Размер контекста"; color: Theme.muted; font.pixelSize: 13 }
                        Text { text: page.contextLabel(page.v(bridge.draft,"context",null)); color: Theme.text; font.pixelSize: 18; font.bold: true }
                        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                        Text { text: "Короткий тест скорости не проверяет работу на полном контексте."; color: Theme.muted; font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        Item { Layout.fillHeight: true }
                        StudioButton { text: "История этой модели"; iconName: "chart-bar"; Layout.fillWidth: true; onClicked: page.mode="history" }
                    }
                }
            }
            RowLayout { visible: page.mode==="setup" && !page.progressing; Layout.fillWidth: true; Layout.preferredHeight: 460; spacing: 12
                StudioCard { Layout.minimumWidth: Math.max(320,(page.width-61)/2); Layout.maximumWidth: Math.max(320,(page.width-61)/2); Layout.fillHeight: true
                    ColumnLayout { anchors.fill: parent; anchors.margins: 18; spacing: 15
                        Text { text: "Найти удобные режимы для этого компьютера"; color: Theme.text; font.pixelSize: 20; font.bold: true; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        Text { text: "Проверим несколько конфигураций и предложим лучшие из измеренных вариантов."; color: Theme.muted; font.pixelSize: 14; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        Repeater { model: ["Проверить базовую скорость","Сравнить поддерживаемые MTP и Draft","Проверить размеры контекста","Повторить проверку кандидатов"]
                            Row { required property string modelData; required property int index; spacing: 14
                                Rectangle { width: 30; height: 30; radius: 15; color: Theme.violet2; border.color: Theme.violet
                                    Text { anchors.centerIn: parent; text: index+1; color: Theme.text; font.pixelSize: 15 }
                                }
                                Text { anchors.verticalCenter: parent.verticalCenter; text: modelData; color: Theme.text; font.pixelSize: 14 }
                            }
                        }
                        Item { Layout.fillHeight: true }
                    }
                }
                ColumnLayout { Layout.minimumWidth: Math.max(320,(page.width-61)/2); Layout.maximumWidth: Math.max(320,(page.width-61)/2); Layout.fillHeight: true; spacing: 10
                    StudioCard { Layout.fillWidth: true; Layout.preferredHeight: 164
                        ColumnLayout { anchors.fill: parent; anchors.margins: 14; spacing: 10
                            Text { text: "Границы исследования"; color: Theme.text; font.pixelSize: 18; font.bold: true }
                            RowLayout { Layout.fillWidth: true
                                Text { text: "Максимальный контекст"; color: Theme.muted; Layout.fillWidth: true }
                                SpinBox { from: 8192; to: 1048576; stepSize: 8192; editable: true; value: page.maxContext; onValueModified: page.maxContext=value }
                            }
                            RowLayout { Layout.fillWidth: true
                                Text { text: "Бюджет времени, мин"; color: Theme.muted; Layout.fillWidth: true }
                                SpinBox { from: 5; to: 240; value: page.budgetMinutes; onValueModified: page.budgetMinutes=value }
                            }
                            Text { text: "Проверяются только поддерживаемые параметры."; color: Theme.muted; font.pixelSize: 12 }
                        }
                    }
                    StudioCard { Layout.fillWidth: true; Layout.preferredHeight: 112; color: "#1b2144"; border.color: Theme.violet
                        ColumnLayout { anchors.fill: parent; anchors.margins: 13; spacing: 4
                            Text { text: "Во время исследования модель будет занята."; color: Theme.text; font.pixelSize: 14; font.bold: true }
                            Text { text: "Чат будет приостановлен. Завершите запросы в OpenCode и других клиентах перед началом."; color: Theme.muted; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 12 }
                            CheckBox { text: "Понимаю влияние на внешние приложения"; checked: page.acknowledge; onToggled: page.acknowledge=checked }
                        }
                    }
                    StudioButton { text: "Начать исследование"; iconName: "player-play"; primary: true; Layout.fillWidth: true; enabled: page.acknowledge && !!page.v(bridge.selectedModel,"id","") && page.v(bridge.selectedModel,"testable",true) && !bridge.busy; onClicked: { bridge.runResearch({max_context:page.maxContext,budget_minutes:page.budgetMinutes,external_use_acknowledged:true}); if (bridge.busy) page.mode="progress" } }
                    StudioButton { text: "Вернуться к запуску"; iconName: "arrow-left"; Layout.fillWidth: true; onClicked: host.navigate(0) }
                    Item { Layout.fillHeight: true }
                }
            }
            RowLayout { visible: page.progressing; Layout.fillWidth: true; Layout.preferredHeight: 448; spacing: 12
                StudioCard { Layout.preferredWidth: 240; Layout.fillHeight: true
                    ColumnLayout { anchors.fill: parent; anchors.margins: 16; spacing: 14
                        Text { text: "Этапы исследования"; color: Theme.text; font.pixelSize: 18; font.bold: true }
                        Text { text: page.v(bridge.research,"phase","Идёт исследование"); color: Theme.muted; font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        Repeater { model: ["Базовый замер","MTP и Draft","Контекст и память","Итоговые режимы"]
                            Row { required property string modelData; required property int index; spacing: 10
                                StudioIcon { name: "info-circle" }
                                Text { text: modelData; color: Theme.text; font.pixelSize: 14 }
                            }
                        }
                        Item { Layout.fillHeight: true }
                    }
                }
                ColumnLayout { Layout.fillWidth: true; Layout.fillHeight: true; spacing: 11
                    StudioCard { Layout.fillWidth: true; Layout.preferredHeight: 196
                        ColumnLayout { anchors.fill: parent; anchors.margins: 16; spacing: 13
                            Text { text: "Текущий замер"; color: Theme.muted; font.pixelSize: 14 }
                            Text { text: page.v(bridge.research,"message",page.v(bridge.research,"phase","Идёт исследование…") + (page.v(bridge.research,"phase","").indexOf("Проверяем")>=0 && page.v(bridge.research,"context",null) ? "  •  " + page.contextLabel(page.v(bridge.research,"context",null)) : "")); color: Theme.text; font.pixelSize: 22; font.bold: true; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            ProgressBar { Layout.fillWidth: true; from: 0; to: 1; value: Number(page.v(bridge.research,"progress",Math.max(0,Number(page.v(bridge.research,"index",1))-1)/Math.max(1,Number(page.v(bridge.research,"total",1))))) }
                            Text { text: "Завершено " + page.v(bridge.research,"completed",Math.max(0,Number(page.v(bridge.research,"index",1))-1)) + " из " + page.v(bridge.research,"total","?"); color: Theme.muted; font.pixelSize: 13 }
                        }
                    }
                    StudioCard { Layout.fillWidth: true; Layout.fillHeight: true
                        ColumnLayout { anchors.fill: parent; anchors.margins: 16; spacing: 8
                            Text { text: "Завершённые конфигурации"; color: Theme.text; font.pixelSize: 17; font.bold: true }
                            ListView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; model: page.v(bridge.research,"results",bridge.results || [])
                                delegate: Rectangle { required property var modelData; width: ListView.view.width; height: 34; color: "transparent"; border.color: Theme.border
                                    RowLayout { anchors.fill: parent; anchors.margins: 6
                                        Text { text: page.contextLabel(page.v(modelData,"context",null)); color: Theme.text; Layout.preferredWidth: 72 }
                                        Text { text: page.v(page.v(modelData,"config",{}),"mtp",false) ? "MTP  •  Draft " + page.v(page.v(modelData,"config",{}),"draft","?") : page.statusLabel(page.v(modelData,"status","Замер")); color: Theme.muted; Layout.fillWidth: true; elide: Text.ElideRight }
                                        Text { text: page.shown(page.v(modelData,"speed",null)," ток/с"); color: Theme.text }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            RowLayout { visible: page.mode==="history" && !page.progressing; Layout.fillWidth: true; spacing: 12
                Text { text: "Модель"; color: Theme.text; font.pixelSize: 13 }
                SearchSelect { Layout.preferredWidth: 320; entries: (bridge.models || []).filter(function(m) { return m.testable!==false }); selectedId: page.v(bridge.selectedModel,"id",""); placeholder: "Выберите модель"; iconName: "cube"; onSelected: (id) => { page.researchFilter=""; bridge.clearResearchView(); bridge.selectModel(id) } }
                StudioButton { visible: !!page.researchFilter; text: "Все исследования"; subtle: true; onClicked: { page.researchFilter=""; bridge.clearResearchView() } }
                Item { Layout.fillWidth: true }
                Text { text: "Результаты сохраняются автоматически"; color: Theme.muted; font.pixelSize: 12 }
            }
            RowLayout { visible: page.mode==="history" && !page.progressing; Layout.fillWidth: true; spacing: 8
                ComboBox { Layout.preferredWidth: 176; model: page.contextOptions(); textRole: "text"; valueRole: "value"; currentIndex: Math.max(0,model.findIndex(function(x) { return x.value===page.contextFilter })); onActivated: page.contextFilter=String(currentValue) }
                ComboBox { Layout.preferredWidth: 152; model: [{text:"Все статусы",value:""},{text:"Сохранён",value:"completed"},{text:"Ошибка",value:"error"},{text:"Остановлен",value:"cancelled"},{text:"Прерван",value:"stopped"}]; textRole: "text"; valueRole: "value"; currentIndex: Math.max(0,model.findIndex(function(x) { return x.value===page.statusFilter })); onActivated: page.statusFilter=String(currentValue) }
                ComboBox { Layout.preferredWidth: 152; model: ["Все окружения","ollama","llama.cpp"]; currentIndex: Math.max(0,model.indexOf(page.backendFilter)); onActivated: page.backendFilter=index===0 ? "" : currentText }
                Item { Layout.fillWidth: true }
                Text { text: (page.researchFilter ? (bridge.researchResults || []) : (bridge.results || [])).length + " загружено"; color: Theme.muted; font.pixelSize: 12 }
            }
            RowLayout { visible: page.mode==="history" && !page.progressing; Layout.fillWidth: true; spacing: 9
                Repeater { model: ["speed","balanced","context"]
                    StudioCard { required property string modelData; required property int index; property var rec: {
                            let a=bridge.recommendations || []; for(let i=0;i<a.length;i++) if(a[i].key===modelData) return a[i]; return null
                        }
                        Layout.fillWidth: true; Layout.preferredHeight: 136
                        ColumnLayout { anchors.fill: parent; anchors.margins: 13; spacing: 7
                            Text { text: rec ? page.v(rec,"title","") : ["Максимальная скорость","Сбалансированный","Максимальный контекст"][index]; color: Theme.text; font.pixelSize: 14; font.bold: true }
                            Text { text: rec && rec.available ? page.contextLabel(rec.context) + "  •  " + page.shown(rec.speed," ток/с") : "Нет проверенного режима"; color: Theme.muted; font.pixelSize: 15 }
                            Item { Layout.fillHeight: true }
                            StudioButton { visible: !!rec && !!rec.available; text: "Применить для запуска"; iconName: "player-play"; primary: true; Layout.fillWidth: true; onClicked: { bridge.applyRecommendation(modelData); host.navigate(0) } }
                        }
                    }
                }
            }
            StudioCard { visible: page.mode==="history" && !page.progressing; Layout.fillWidth: true; Layout.preferredHeight: 260
                ColumnLayout { anchors.fill: parent; anchors.margins: 14; spacing: 9
                    Text { text: page.researchFilter ? "Замеры выбранного исследования" : "Все замеры"; color: Theme.text; font.pixelSize: 18; font.bold: true }
                    ListView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; model: page.filteredResults
                        delegate: Rectangle { required property var modelData; width: ListView.view.width;
                            height: 56; color: page.v(bridge.selectedResult,"id","")===page.v(modelData,"id","") ? "#202648" : "transparent"; border.color: Theme.border
                            RowLayout { anchors.fill: parent; anchors.margins: 10
                                Text { text: page.contextLabel(page.v(modelData,"context",null)); color: Theme.text; Layout.preferredWidth: 96 }
                                Text { text: page.v(modelData,"model_name",""); color: Theme.muted; Layout.fillWidth: true; elide: Text.ElideRight }
                                Text { text: page.shown(page.v(modelData,"speed",null)," ток/с"); color: Theme.text; Layout.preferredWidth: 128 }
                                Text { text: page.shown(page.v(modelData,"ttft",null)," с"); color: Theme.muted; Layout.preferredWidth: 72 }
                                Text { text: page.statusLabel(page.v(modelData,"status","")); color: page.v(modelData,"status","")==="completed" ? Theme.green : Theme.muted }
                            }
                            MouseArea { anchors.fill: parent; onClicked: bridge.selectResult(page.v(modelData,"id","")) }
                        }
                    }
                    StudioButton { visible: !page.researchFilter && !!bridge.hasMoreResults; text: "Загрузить ещё замеры"; iconName: "chevron-down"; Layout.alignment: Qt.AlignHCenter; onClicked: bridge.loadMoreResults() }
                    Text { text: page.v(bridge.selectedResult,"id","") ? "Выбранный замер: " + page.contextLabel(page.v(bridge.selectedResult,"context",null)) + "  •  " + page.shown(page.v(bridge.selectedResult,"speed",null)," ток/с") + (page.v(bridge.selectedResult,"legacy_source","") ? "  •  Импортированный отчёт" : "") : "Для выбранной модели и фильтров нет сохранённых замеров. Отчёт появится после сохранения результата."; color: Theme.muted; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 12 }
                    RowLayout { visible: !!page.v(bridge.selectedResult,"id",""); Layout.fillWidth: true; spacing: 19
                        Text { text: "Среда: " + page.v(page.v(bridge.selectedResult,"config",{}),"runtime_name","Нет данных"); color: Theme.muted; font.pixelSize: 12 }
                        Text { text: "Первый токен: " + page.shown(page.v(bridge.selectedResult,"ttft",null)," с"); color: Theme.muted; font.pixelSize: 12 }
                        Text { text: "VRAM: " + page.shown(page.v(bridge.selectedResult,"vram_gb",null)," ГБ"); color: Theme.muted; font.pixelSize: 12 }
                    }
                    RowLayout { Layout.fillWidth: true
                        StudioButton { objectName: "copyAllReportsButton"; text: page.researchFilter ? "Исследование целиком" : "Скопировать все отчёты"; iconName: "copy"; enabled: !!page.researchFilter || !!page.v(bridge.selectedModel,"id",""); onClicked: bridge.copyReports({research_id:page.researchFilter, context:page.contextFilter,status:page.statusFilter,backend:page.backendFilter}) }
                        Item { Layout.fillWidth: true }
                        StudioButton { objectName: "copyReportButton"; text: "Скопировать выбранный"; iconName: "copy"; enabled: !!page.v(bridge.selectedResult,"id",""); onClicked: bridge.copyReport() }
                        StudioButton { objectName: "exportReportButton"; text: "Экспортировать…"; iconName: "download"; enabled: !!page.v(bridge.selectedResult,"id",""); onClicked: bridge.exportReport() }
                    }
                }
            }
            StudioCard { visible: page.mode==="history" && !page.progressing && (bridge.researchJobs || []).length>0; Layout.fillWidth: true; Layout.preferredHeight: 184
                ColumnLayout { anchors.fill: parent; anchors.margins: 14; spacing: 6
                    Text { text: "Задания исследования"; color: Theme.text; font.pixelSize: 18; font.bold: true }
                    ListView { Layout.fillWidth: true; Layout.fillHeight: true; model: bridge.researchJobs || []; clip: true
                        delegate: Rectangle { required property var modelData; width: ListView.view.width; height: 62; color: "transparent"; border.color: Theme.border
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: page.showResearch(modelData) }
                            RowLayout { anchors.fill: parent; anchors.margins: 6
                                ColumnLayout { Layout.fillWidth: true; spacing: 3
                                    Text { text: page.v(modelData,"created_at",page.v(modelData,"id","Задание")); color: Theme.text; Layout.fillWidth: true; elide: Text.ElideRight }
                                    Text { text: page.jobReason(modelData); color: Theme.muted; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideRight
                                        HoverHandler { id: reasonHover }
                                        ToolTip.visible: reasonHover.hovered && text.length>0
                                        ToolTip.text: text
                                    }
                                }
                                Text { text: page.statusLabel(page.v(modelData,"status","")); color: Theme.muted; Layout.preferredWidth: 84 }
                                StudioButton { text: "Скопировать исследование"; iconName: "copy"; buttonHeight: 27; onClicked: bridge.copyReports({research_id:modelData.id}) }
                                StudioButton { text: "Продолжить"; iconName: "refresh"; buttonHeight: 27; visible: ["cancelled","stopped","interrupted","failed"].indexOf(page.v(modelData,"status",""))>=0; onClicked: { bridge.resumeResearch(page.v(modelData,"id","")); if (bridge.busy) page.mode="progress" } }
                            }
                        }
                    }
                }
            }
            StudioButton { visible: page.mode==="history" && !page.progressing; text: "Вернуться к исследованию"; iconName: "arrow-left"; onClicked: page.mode="quick" }
            Item { Layout.preferredHeight: 12 }
        }
    }
    Dialog { id: stopDialog; title: "Остановить исследование?"; modal: true; anchors.centerIn: parent; standardButtons: Dialog.Yes | Dialog.Cancel; onAccepted: bridge.cancel()
        contentItem: Text { text: "Завершённые замеры останутся в истории."; color: Theme.text }
        background: Rectangle { color: Theme.panel2; border.color: Theme.border; radius: 6 }
    }
}
