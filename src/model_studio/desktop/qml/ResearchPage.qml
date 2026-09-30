import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

Item {
    id: page
    objectName: "researchPage"
    property var bridge
    property var host
    property string view: "builder"
    property int planIndex: 0
    property var planSelection: ({})
    property string resultId: ""
    property string planFilter: "all"
    readonly property var draft: bridge.researchDraft || ({})
    readonly property var preview: bridge.researchPreview || ({status:"empty", rows:[]})
    readonly property var planRows: preview.rows || []
    readonly property var results: bridge.experimentResults || []
    readonly property bool ready: val(preview,"status","") === "ready"
    readonly property bool allKnown: ready && Number(val(preview,"checks_total",0)) === 0 && Number(val(preview,"total",0)) > 0
    readonly property bool running: ["running","starting","cancelling"].indexOf(val(bridge.research,"status","")) >= 0
    function val(o,k,d) { let x=o && o[k]; return x===undefined || x===null || x==="" ? d : x }
    function arr(o,k) { let x=val(o,k,[]); return x && typeof x.length==="number" ? Array.prototype.slice.call(x) : [] }
    function context(n) { let x=Number(n); return Number.isFinite(x) && x>=1024 ? (x%1024===0 ? x/1024 : Math.round(x/1024))+"K" : String(n || "—") }
    function kv(k) { return ({f16:"F16",q8_0:"Q8",q4_0:"Q4"})[String(k).toLowerCase()] || String(k || "—").toUpperCase() }
    function mtp(n) { return Number(n)===0 ? "Выкл." : "Draft "+n }
    function planMtp(r) { return val(r,"mtp",false) ? mtp(val(r,"draft",1)) : mtp(0) }
    function fmt(n,p) { let x=Number(n); return n===undefined || n===null || n==="" || !Number.isFinite(x) ? "—" : x.toLocaleString(Qt.locale("ru_RU"),"f",p===undefined ? 1 : p) }
    function longVerified(r) { let x=val(r,"long_context",{}); return x && x.validated===true }
    function studyId() { let jobs=val(bridge,"experimentJobs",[]); return val(bridge.research,"id",jobs.length ? val(jobs[0],"id","") : "") }
    function currentJob() { let jobs=val(bridge,"experimentJobs",[]), id=studyId(); for (let j of jobs) if (val(j,"id","")===id) return j; return ({}) }
    function selectedReport() { let id=val(selectedResult(),"id",""); if (id) bridge.selectResult(id); bridge.copyExperimentReport("selected") }
    function selectedExport() { let id=val(selectedResult(),"id",""); if (id) bridge.selectResult(id); bridge.exportExperimentReport("selected") }
    function reasoningIndex() { let c=val(draft,"config",{}), mode=val(c,"reasoning","auto"), budget=Number(val(c,"reasoning_budget",0)); return mode==="off" ? 0 : mode==="auto" ? 4 : budget===2048 ? 1 : budget===4096 ? 2 : budget===8192 ? 3 : 5 }
    function reasoningAllowed(o) { return o.mode==="auto" || arr(draft,"capabilities").indexOf("reasoning")>=0 && (!o.budget || arr(draft,"capabilities").indexOf("reasoning-budget")>=0) }
    function statusLabel(s) { return s==="history" ? "Из истории" : s==="measure" ? "Проверить" : s==="unavailable" ? "Недоступно" : "Не выбрано" }
    function stateColor(s) { return s==="history" ? Theme.green : s==="measure" ? "#a97cff" : Theme.faint }
    function selectedPlan() { return val(planSelection,"key","") ? planSelection : planRows.length ? planRows[Math.min(planIndex,planRows.length-1)] : ({}) }
    function selectedResult() { for (let r of results) if (String(val(r,"id",""))===resultId) return r; return results.length ? results[0] : ({}) }
    function showResults() { view="results"; if (results.length) { resultId=String(val(results[0],"id","")); bridge.selectResult(resultId) } }
    function start() { if (!ready) return; bridge.startExperiment() }
    Component.onCompleted: { bridge.ensureResearchDraft(); if (running) view="progress" }
    onPreviewChanged: planSelection=({})
    onResultsChanged: {
        if (results.length && !results.some(function(r) { return String(r.id)===page.resultId })) {
            resultId=String(results[0].id)
            bridge.selectResult(resultId)
        }
    }
    onVisibleChanged: if (visible) bridge.ensureResearchDraft()
    onRunningChanged: if (running) view="progress"
    Connections { target: bridge; function onChanged() { if (page.view==="progress" && !bridge.busy && page.val(bridge.research,"status","")!=="") page.showResults() } }

    ScrollView { anchors.fill: parent; clip: true; ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout { x: Theme.pagePadding; width: Math.max(780,page.width-2*Theme.pagePadding); spacing: 12
            Item { Layout.preferredHeight: 3 }
            StudioButton { visible: page.view==="plan"; text: "К параметрам"; iconName: "arrow-left"; subtle: true; onClicked: page.view="builder" }
            ColumnLayout { spacing: 2
                Text { text: page.view==="plan" ? "План исследования" : page.view==="results" ? "Результаты исследования" : page.view==="progress" ? "Исследование выполняется" : "Исследование"; color: Theme.text; font.pixelSize: 28; font.bold: true }
                Text { text: page.view==="results" ? "Сравните конфигурации и выберите подходящую для запуска." : page.view==="plan" ? "Проверьте все сочетания и источники результатов." : page.view==="progress" ? "Готовые замеры сохраняются автоматически." : "Выберите, что сравнить. Готовые результаты используем повторно."; color: Theme.muted; font.pixelSize: 13 }
            }
            RowLayout { visible: page.view==="builder" || page.view==="results"; Layout.fillWidth: true; spacing: 12
                Button { id: builderTab; objectName: "newExperimentTab"; text: "Новое исследование"; Layout.preferredWidth: 164; Layout.preferredHeight: 36; onClicked: page.view="builder"
                    contentItem: Text { text: builderTab.text; color: page.view==="builder" ? Theme.text : Theme.muted; font.pixelSize: 14; font.bold: page.view==="builder"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    background: Item { Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 3; color: "#8e63ff"; visible: page.view==="builder" } }
                }
                Button { id: resultsTab; objectName: "experimentResultsTab"; text: "Результаты"; Layout.preferredWidth: 112; Layout.preferredHeight: 36; onClicked: page.showResults()
                    contentItem: Text { text: resultsTab.text; color: page.view==="results" ? Theme.text : Theme.muted; font.pixelSize: 14; font.bold: page.view==="results"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    background: Item { Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 3; color: "#8e63ff"; visible: page.view==="results" } }
                }
                Item { Layout.fillWidth: true }
                StudioButton { visible: page.view==="results" || page.running; text: "Задания"; iconName: "database"; onClicked: jobsDialog.open() }
            }
            StudioCard { Layout.fillWidth: true; Layout.preferredHeight: 62
                RowLayout { anchors.fill: parent; anchors.margins: 11; spacing: 12
                    SearchSelect { Layout.preferredWidth: 205; entries: (bridge.models || []).filter(function(e) { return e.available && e.testable!==false }); selectedId: String(page.val(page.draft,"model_id","")); placeholder: "Выберите модель"; iconName: "cube"; onSelected: (id) => bridge.selectResearchModel(id) }
                    Rectangle { Layout.preferredWidth: 1; Layout.fillHeight: true; color: Theme.border }
                    ColumnLayout { Layout.fillWidth: true; spacing: 2
                        Text { text: "Общие условия:  "+page.val(page.val(page.draft,"config",{}),"runtime_name","Auto")+"  •  Рассуждение: "+page.val(page.val(page.draft,"config",{}),"reasoning","auto")+"  •  Изображения: "+(page.val(page.val(page.draft,"config",{}),"mmproj","") ? "вкл." : "выкл.")+"  •  GPU: "+(Number(page.val(page.val(page.draft,"config",{}),"gpu_layers",99))===99 ? "Auto" : page.val(page.val(page.draft,"config",{}),"gpu_layers",0)); color: Theme.text; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
                        Text { text: "Условия одинаковы для всех вариантов."; color: Theme.faint; font.pixelSize: 11 }
                    }
                    StudioButton { text: "Изменить"; iconName: "settings"; subtle: true; onClicked: conditionsDialog.open() }
                }
            }
            RowLayout { visible: page.view==="builder"; Layout.fillWidth: true; spacing: 12
                StudioCard { Layout.fillWidth: true; Layout.preferredWidth: 650; Layout.preferredHeight: Math.max(498,builderContent.implicitHeight+30)
                    ColumnLayout { id: builderContent; anchors.fill: parent; anchors.margins: 15; spacing: 7
                        Text { text: "Конструктор эксперимента"; color: Theme.text; font.pixelSize: 20; font.bold: true }
                        Text { text: "Что сравнить"; color: Theme.text; font.pixelSize: 15; font.bold: true }
                        ColumnLayout { Layout.fillWidth: true; spacing: 0
                            Repeater { model: [
                                {key:"contexts",label:"Контекст",choices:[{text:"32K",value:32768},{text:"64K",value:65536},{text:"96K",value:98304},{text:"128K",value:131072}]},
                                {key:"kv_types",label:"Память контекста",choices:[{text:"F16",value:"f16"},{text:"Q8",value:"q8_0"},{text:"Q4",value:"q4_0"}]},
                                {key:"mtp_variants",label:"Ускорение MTP",choices:[{text:"Выкл.",value:0},{text:"Draft 1",value:1},{text:"Draft 2",value:2},{text:"Draft 4",value:4}]}
                            ]
                                delegate: Rectangle { id: optionRow; required property var modelData; Layout.fillWidth: true; Layout.preferredHeight: Math.max(59,choiceFlow.childrenRect.height+16); color: Theme.panel2; border.color: Theme.border
                                    property var selected: page.arr(page.draft,modelData.key)
                                    function label(v) { return modelData.key==="contexts" ? page.context(v) : modelData.key==="kv_types" ? page.kv(v) : page.mtp(v) }
                                    RowLayout { anchors.fill: parent; anchors.margins: 8; spacing: 7
                                        Text { text: optionRow.modelData.label; color: Theme.text; font.pixelSize: 12; font.bold: true; Layout.preferredWidth: 130 }
                                        Flow { id: choiceFlow; Layout.fillWidth: true; spacing: 4
                                            Repeater { model: optionRow.selected
                                                delegate: Rectangle { required property var modelData; width: tag.implicitWidth+18; height: 27; radius: 4; color: Theme.violet2; border.color: Theme.violet
                                                    Text { id: tag; anchors.centerIn: parent; text: optionRow.label(modelData)+" ×"; color: Theme.text; font.pixelSize: 11 }
                                                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: { let a=optionRow.selected.slice(); let i=a.indexOf(modelData); if (i>=0 && a.length>1) { a.splice(i,1); page.bridge.setResearchOption(optionRow.modelData.key,a) } } }
                                                }
                                            }
                                        }
                                        Text { text: optionRow.selected.length+" знач."; color: Theme.muted; font.pixelSize: 11 }
                                        StudioButton { objectName: "researchEdit"+optionRow.modelData.key; text: "Изменить"; subtle: true; buttonHeight: 29; onClicked: chooser.open() }
                                    }
                                    Popup { id: chooser; objectName: "researchChoicePopup"+optionRow.modelData.key; x: Math.max(0,optionRow.width-width); y: optionRow.height+3; width: 260; padding: 11; closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
                                        background: Rectangle { color: Theme.panel2; border.color: Theme.violet; radius: 6 }
                                        contentItem: ColumnLayout { spacing: 4
                                            Text { text: optionRow.modelData.label; color: Theme.text; font.bold: true }
                                            Repeater { model: optionRow.modelData.choices
                                                delegate: ResearchCheck { required property var modelData; text: modelData.text; checked: optionRow.selected.indexOf(modelData.value)>=0
                                                    enabled: checked || optionRow.modelData.key==="contexts" || optionRow.modelData.key==="kv_types" && (modelData.value==="f16" || page.arr(page.draft,"capabilities").indexOf("kv-cache")>=0) || optionRow.modelData.key==="mtp_variants" && (modelData.value===0 || page.arr(page.draft,"capabilities").indexOf("mtp")>=0)
                                                    onClicked: { let a=optionRow.selected.slice(); let i=a.indexOf(modelData.value); if (checked && i<0) a.push(modelData.value); if (!checked && i>=0 && a.length>1) a.splice(i,1); page.bridge.setResearchOption(optionRow.modelData.key,a) }
                                                }
                                            }
                                            RowLayout { Layout.fillWidth: true; visible: optionRow.modelData.key!=="kv_types"
                                                TextField { id: customValue; Layout.fillWidth: true; placeholderText: optionRow.modelData.key==="contexts" ? "Токены" : "Draft 1–32"; validator: IntValidator { bottom: 1; top: optionRow.modelData.key==="contexts" ? 2097152 : 32 } }
                                                StudioButton { text: "+"; buttonHeight: 28; enabled: customValue.acceptableInput && (optionRow.modelData.key==="contexts" || page.arr(page.draft,"capabilities").indexOf("mtp")>=0); onClicked: { let a=optionRow.selected.slice(); let n=Number(customValue.text); if (a.indexOf(n)<0) { a.push(n); a.sort(function(x,y){return x-y}); page.bridge.setResearchOption(optionRow.modelData.key,a) } customValue.text="" } }
                                            }
                                            Text { visible: optionRow.modelData.key==="mtp_variants" && page.arr(page.draft,"capabilities").indexOf("mtp")<0; text: page.val(page.draft,"mtp_reason","MTP недоступен для модели."); color: Theme.muted; font.pixelSize: 11; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                        }
                                    }
                                }
                            }
                        }
                        Text { text: "Одно значение фиксируем. Несколько — сравниваем все сочетания."; color: Theme.faint; font.pixelSize: 11 }
                        Text { text: "Что проверить"; color: Theme.text; font.pixelSize: 15; font.bold: true }
                        ResearchCheck { text: "Агентский сценарий"; checked: page.val(page.val(page.draft,"checks",{}),"speed",true); onClicked: bridge.setResearchOption("checks",{speed:checked,long_context:page.val(page.val(page.draft,"checks",{}),"long_context",true)}) }
                        Text { text: "6 ходов · история 4–24K · меньше секунд — лучше"; color: Theme.muted; font.pixelSize: 11; Layout.leftMargin: 36 }
                        ResearchCheck { text: "Работа на полном контексте"; checked: page.val(page.val(page.draft,"checks",{}),"long_context",true); onClicked: bridge.setResearchOption("checks",{speed:page.val(page.val(page.draft,"checks",{}),"speed",true),long_context:checked}) }
                        Text { text: "Длинный вход без усечения"; color: Theme.muted; font.pixelSize: 11; Layout.leftMargin: 36 }
                        Item { Layout.fillHeight: true }
                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }
                        RowLayout { Layout.fillWidth: true
                            StudioIcon { name: "settings" }
                            Text { text: "Условия измерения: "+page.val(page.draft,"runs",1)+" повтора"; color: Theme.muted; font.pixelSize: 11; Layout.fillWidth: true }
                            StudioButton { text: "Изменить"; subtle: true; buttonHeight: 29; onClicked: conditionsDialog.open() }
                        }
                    }
                }
                StudioCard { Layout.preferredWidth: 330; Layout.preferredHeight: 498
                    ColumnLayout { anchors.fill: parent; anchors.margins: 15; spacing: 11
                        Text { text: "План исследования"; color: Theme.text; font.pixelSize: 19; font.bold: true }
                        Text { text: page.ready ? page.val(page.preview,"total",0)+" конфигураций" : page.val(page.preview,"status","")==="error" ? "Проверьте параметры" : "Сверяем с историей…"; color: page.val(page.preview,"status","")==="error" ? Theme.red : "#a97cff"; font.pixelSize: 22; font.bold: true; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        Text { text: page.arr(page.draft,"contexts").length+" контекста × "+page.arr(page.draft,"kv_types").length+" типа памяти × "+page.arr(page.draft,"mtp_variants").length+" MTP"; color: Theme.muted; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        Repeater { model: [{label:"Уже проверены",key:"known",icon:"check"},{label:"Нужно дополнить",key:"supplement",icon:"refresh"},{label:"Новые",key:"new",icon:"plus"}]
                            delegate: RowLayout { required property var modelData; Layout.fillWidth: true; spacing: 9
                                StudioIcon { name: modelData.icon; width: 17; height: 17 }
                                Text { text: page.val(page.preview,modelData.key,"—"); color: Theme.text; font.pixelSize: 16; font.bold: true; Layout.preferredWidth: 22 }
                                Text { text: modelData.label; color: Theme.muted; font.pixelSize: 12; Layout.fillWidth: true }
                            }
                        }
                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }
                        Text { text: "Осталось выполнить"; color: Theme.muted; font.pixelSize: 12; font.bold: true }
                        Text { text: page.val(page.preview,"speed_checks","—")+" агентских сценариев"; color: Theme.text; font.pixelSize: 12 }
                        Text { text: page.val(page.preview,"long_checks","—")+" проверок длинного входа"; color: Theme.text; font.pixelSize: 12 }
                        StudioButton { text: "Посмотреть план →"; subtle: true; enabled: page.ready && page.planRows.length>0; onClicked: page.view="plan" }
                        Item { Layout.fillHeight: true }
                        ResearchCheck { text: "Использовать готовые результаты"; checked: page.val(page.draft,"skip_existing",true); onClicked: bridge.setResearchOption("skip_existing",checked) }
                        Text { visible: page.arr(page.preview,"warnings").length>0; text: page.arr(page.preview,"warnings").join("\n"); color: Theme.muted; font.pixelSize: 11; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        Text { text: page.ready ? "Готовые результаты сохраняются автоматически." : page.val(page.preview,"status","")==="error" ? page.val(page.preview,"error","Ошибка расчёта") : "Ожидайте расчёта плана перед запуском."; color: Theme.muted; font.pixelSize: 11; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        StudioButton { objectName: "startExperimentButton"; Layout.fillWidth: true; text: !page.ready ? "План ещё не готов" : page.allKnown ? "Посмотреть результаты" : "Запустить "+page.val(page.preview,"checks_total",0)+" проверок"; iconName: page.allKnown ? "chart-bar" : "player-play"; primary: true; enabled: page.ready && !bridge.busy && (page.allKnown || Number(page.val(page.preview,"checks_total",0))>0); onClicked: page.start() }
                    }
                }
            }
            RowLayout { visible: page.view==="plan"; Layout.fillWidth: true; spacing: 11
                ColumnLayout { Layout.fillWidth: true; spacing: 8
                    Text { text: page.val(page.draft,"model_name","Модель")+"  •  "+page.val(page.preview,"total",0)+" конфигураций  •  "+page.val(page.preview,"checks_total",0)+" новых проверок"; color: Theme.muted; font.pixelSize: 14 }
                    RowLayout {
                        StudioButton { text: "Все "+page.planRows.length; primary: page.planFilter==="all"; subtle: page.planFilter!=="all"; buttonHeight: 29; onClicked: page.planFilter="all" }
                        StudioButton { text: "Требуют проверки"; primary: page.planFilter==="pending"; subtle: page.planFilter!=="pending"; buttonHeight: 29; onClicked: page.planFilter="pending" }
                    }
                    StudioCard { Layout.fillWidth: true; Layout.preferredHeight: 445
                        ColumnLayout { anchors.fill: parent; anchors.margins: 7; spacing: 0
                            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 34; color: Theme.panel2
                                RowLayout { anchors.fill: parent; anchors.leftMargin: 8; spacing: 6
                                    Text { text: "Контекст"; color: Theme.muted; Layout.preferredWidth: 70; font.pixelSize: 11 }
                                    Text { text: "Память"; color: Theme.muted; Layout.preferredWidth: 65; font.pixelSize: 11 }
                                    Text { text: "MTP"; color: Theme.muted; Layout.preferredWidth: 75; font.pixelSize: 11 }
                                    Text { text: "Сценарий, с"; color: Theme.muted; Layout.fillWidth: true; font.pixelSize: 11 }
                                    Text { text: "Длинный вход"; color: Theme.muted; Layout.fillWidth: true; font.pixelSize: 11 }
                                }
                            }
                            ListView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; ScrollBar.vertical: ScrollBar {} model: page.planFilter==="all" ? page.planRows : page.planRows.filter(function(r){return r.speed_status==="measure" || r.long_status==="measure"})
                                delegate: Rectangle { required property var modelData; required property int index; width: ListView.view.width; height: 32; color: page.val(page.selectedPlan(),"key","")===page.val(modelData,"key","") ? Theme.violet2 : index%2 ? Theme.panel2 : "transparent"
                                    RowLayout { anchors.fill: parent; anchors.leftMargin: 8; spacing: 6
                                        Text { text: page.context(modelData.context); color: Theme.text; Layout.preferredWidth: 70; font.pixelSize: 12 }
                                        Text { text: page.kv(modelData.kv_type); color: Theme.text; Layout.preferredWidth: 65; font.pixelSize: 12 }
                                        Text { text: page.planMtp(modelData); color: Theme.text; Layout.preferredWidth: 75; font.pixelSize: 12 }
                                        Text { text: "●  "+page.statusLabel(modelData.speed_status); color: page.stateColor(modelData.speed_status); Layout.fillWidth: true; font.pixelSize: 11 }
                                        Text { text: "●  "+page.statusLabel(modelData.long_status); color: page.stateColor(modelData.long_status); Layout.fillWidth: true; font.pixelSize: 11 }
                                    }
                                    MouseArea { anchors.fill: parent; onClicked: { page.planIndex=index; page.planSelection=modelData } }
                                }
                            }
                        }
                    }
                }
                StudioCard { Layout.preferredWidth: 295; Layout.preferredHeight: 515
                    ColumnLayout { anchors.fill: parent; anchors.margins: 14; spacing: 11
                        Text { text: page.context(page.val(page.selectedPlan(),"context",0))+"  •  "+page.kv(page.val(page.selectedPlan(),"kv_type",""))+"  •  "+page.planMtp(page.selectedPlan()); color: Theme.text; font.pixelSize: 17; font.bold: true }
                        Text { text: "Сценарий: "+page.statusLabel(page.val(page.selectedPlan(),"speed_status","off")); color: page.stateColor(page.val(page.selectedPlan(),"speed_status","off")); font.pixelSize: 13 }
                        Text { text: "Длинный вход: "+page.statusLabel(page.val(page.selectedPlan(),"long_status","off")); color: page.stateColor(page.val(page.selectedPlan(),"long_status","off")); font.pixelSize: 13 }
                        Text { text: page.val(page.selectedPlan(),"speed_status","")==="history" ? "Сопоставимый замер сценария найден в истории." : "Будет выполнен новый замер при выбранных условиях."; color: Theme.muted; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }
                        Text { text: "Общие условия"; color: Theme.text; font.pixelSize: 13; font.bold: true }
                        Text { text: "Среда: "+page.val(page.val(page.draft,"config",{}),"runtime_name","—")+"\nРассуждение: "+page.val(page.val(page.draft,"config",{}),"reasoning","Auto")+"\nGPU: "+page.val(page.val(page.draft,"config",{}),"gpu_layers","Auto"); color: Theme.muted; font.pixelSize: 12; lineHeight: 1.4 }
                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }
                        Text { text: page.val(page.preview,"speed_checks",0)+" агентских сценариев  •  "+page.val(page.preview,"long_checks",0)+" проверок длинного входа"; color: Theme.muted; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        Item { Layout.fillHeight: true }
                        StudioButton { Layout.fillWidth: true; primary: true; enabled: page.ready && !bridge.busy; text: page.allKnown ? "Посмотреть результаты" : "Запустить "+page.val(page.preview,"checks_total",0)+" проверок"; iconName: "player-play"; onClicked: page.start() }
                        StudioButton { Layout.fillWidth: true; text: "Вернуться к параметрам"; onClicked: page.view="builder" }
                    }
                }
            }
            StudioCard { visible: page.view==="progress"; Layout.fillWidth: true; Layout.preferredHeight: 270
                ColumnLayout { anchors.fill: parent; anchors.margins: 20; spacing: 13
                    Text { text: "Проверяем выбранные конфигурации"; color: Theme.text; font.pixelSize: 20; font.bold: true }
                    Text { text: page.val(bridge.research,"phase","Подготовка")+"  •  "+page.val(bridge.research,"completed",0)+" из "+page.val(bridge.research,"total",0); color: Theme.muted; font.pixelSize: 14 }
                    Text { property var cfg: page.val(bridge.research,"config",{}); text: page.context(cfg.context)+"  •  "+page.kv(cfg.kv_type)+"  •  "+page.mtp(cfg.mtp ? cfg.draft : 0); visible: !!cfg.context; color: Theme.text; font.pixelSize: 14 }
                    Text { text: page.val(bridge.research,"context",0) ? "Сейчас: контекст "+page.context(page.val(bridge.research,"context",0))+"  •  "+page.kv(page.val(page.val(bridge.research,"config",{}),"kv_type",""))+"  •  "+page.planMtp(page.val(bridge.research,"config",{})) : "Ожидаем первую конфигурацию"; color: Theme.text; font.pixelSize: 12 }
                    ProgressBar { Layout.fillWidth: true; from: 0; to: 1; value: Math.max(0,Math.min(1,Number(page.val(bridge.research,"progress",0)))) }
                    Text { text: "Завершённые замеры сохраняются автоматически. Остановленное задание можно продолжить из списка заданий."; color: Theme.muted; font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    RowLayout { StudioButton { text: "Результаты"; iconName: "chart-bar"; onClicked: page.showResults() } StudioButton { text: "Задания"; iconName: "database"; onClicked: jobsDialog.open() } Item { Layout.fillWidth: true } StudioButton { text: "Остановить"; danger: true; iconName: "x"; onClicked: stopDialog.open() } }
                }
            }
            ColumnLayout { visible: page.view==="results"; Layout.fillWidth: true; spacing: 10
                StudioCard { Layout.fillWidth: true; Layout.preferredHeight: 52
                    RowLayout { anchors.fill: parent; anchors.margins: 8; spacing: 12
                        RadioButton { text: "Это исследование"; checked: page.val(bridge,"experimentScope","model")!=="model"; enabled: !!page.studyId(); onClicked: bridge.setExperimentScope(page.studyId()) }
                        RadioButton { text: "Все результаты модели"; checked: page.val(bridge,"experimentScope","model")==="model"; onClicked: bridge.setExperimentScope("model") }
                        Item { Layout.fillWidth: true }
                        Text { text: page.val(page.currentJob(),"errors_count",0)>0 ? page.val(page.currentJob(),"errors_count",0)+" с ошибкой  •  "+page.results.length+" результатов" : page.results.length+" результатов сохранено"; color: page.val(page.currentJob(),"errors_count",0)>0 ? Theme.red : Theme.green; font.pixelSize: 12 }
                    }
                }
                RowLayout { Layout.fillWidth: true; spacing: 9
                    StudioCard { Layout.fillWidth: true; Layout.preferredHeight: 440
                        ColumnLayout { anchors.fill: parent; anchors.margins: 7; spacing: 0
                            RowLayout { Layout.fillWidth: true; Layout.preferredHeight: 50
                                ColumnLayout { Layout.fillWidth: true; spacing: 1
                                    Text { text: "Сравнение конфигураций"; color: Theme.text; font.pixelSize: 18; font.bold: true }
                                    Text { text: "Выберите строку для подробностей"; color: Theme.muted; font.pixelSize: 11 }
                                }
                                StudioButton { text: "Копировать"; iconName: "copy"; onClicked: copyMenu.open()
                                    Menu { id: copyMenu; y: parent.height
                                        MenuItem { text: "Итог исследования"; onTriggered: bridge.copyExperimentReport("study") }
                                        MenuItem { text: "Сводку по модели"; onTriggered: bridge.copyExperimentReport("model") }
                                        MenuItem { text: "Всю историю модели"; onTriggered: bridge.copyExperimentReport("history") }
                                        MenuSeparator {}
                                        MenuItem { text: "Выбранный замер"; onTriggered: page.selectedReport() }
                                        MenuItem { text: "Показанные результаты"; onTriggered: bridge.copyExperimentReport("shown") }
                                    }
                                }
                                StudioButton { text: "Сохранить…"; iconName: "download"; onClicked: saveMenu.open()
                                    Menu { id: saveMenu; y: parent.height
                                        MenuItem { text: "Итог исследования"; onTriggered: bridge.exportExperimentReport("study") }
                                        MenuItem { text: "Сводку по модели"; onTriggered: bridge.exportExperimentReport("model") }
                                        MenuItem { text: "Выбранный замер"; onTriggered: page.selectedExport() }
                                    }
                                }
                            }
                            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 34; color: Theme.panel2
                                RowLayout { anchors.fill: parent; anchors.leftMargin: 6; spacing: 5
                                    Text { text: "Контекст"; color: Theme.muted; Layout.preferredWidth: 64; font.pixelSize: 11 }
                                    Text { text: "Память"; color: Theme.muted; Layout.preferredWidth: 52; font.pixelSize: 11 }
                                    Text { text: "MTP"; color: Theme.muted; Layout.preferredWidth: 67; font.pixelSize: 11 }
                                    Text { text: "Сценарий, с"; color: Theme.muted; Layout.fillWidth: true; font.pixelSize: 11 }
                                    Text { text: "VRAM"; color: Theme.muted; Layout.preferredWidth: 58; font.pixelSize: 11 }
                                    Text { text: "Длинный вход"; color: Theme.muted; Layout.preferredWidth: 92; font.pixelSize: 11 }
                                }
                            }
                            ListView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; ScrollBar.vertical: ScrollBar {} model: page.results
                                delegate: Rectangle { required property var modelData; required property int index; width: ListView.view.width; height: 28; color: String(page.val(modelData,"id",""))===page.resultId ? Theme.violet2 : index%2 ? Theme.panel2 : "transparent"
                                    property var cfg: page.val(modelData,"config",{})
                                    RowLayout { anchors.fill: parent; anchors.leftMargin: 6; spacing: 5
                                        Text { text: page.context(page.val(modelData,"context",page.val(cfg,"context",0))); color: Theme.text; Layout.preferredWidth: 64; font.pixelSize: 11 }
                                        Text { text: page.kv(page.val(cfg,"kv_type","")); color: Theme.text; Layout.preferredWidth: 52; font.pixelSize: 11 }
                                        Text { text: page.mtp(page.val(cfg,"mtp",false) ? page.val(cfg,"draft",1) : 0); color: Theme.text; Layout.preferredWidth: 67; font.pixelSize: 11 }
                                        Text { text: page.fmt(page.val(modelData,"scenario_seconds",null))+" с"; color: Theme.text; Layout.fillWidth: true; font.pixelSize: 11 }
                                        Text { text: page.fmt(page.val(modelData,"vram_gb",null)); color: Theme.text; Layout.preferredWidth: 58; font.pixelSize: 11 }
                                        Text { text: page.longVerified(modelData) ? "Проверен" : "—"; color: page.longVerified(modelData) ? Theme.green : Theme.faint; Layout.preferredWidth: 92; font.pixelSize: 11 }
                                    }
                                    MouseArea { anchors.fill: parent; onClicked: { page.resultId=String(page.val(modelData,"id","")); bridge.selectResult(page.resultId) } }
                                }
                            }
                        }
                    }
                    StudioCard { Layout.preferredWidth: 265; Layout.preferredHeight: 440
                        ColumnLayout { anchors.fill: parent; anchors.margins: 14; spacing: 9
                            Text { text: page.context(page.val(page.selectedResult(),"context",page.val(page.val(page.selectedResult(),"config",{}),"context",0)))+"  •  "+page.kv(page.val(page.val(page.selectedResult(),"config",{}),"kv_type",""))+"  •  "+page.mtp(page.val(page.val(page.selectedResult(),"config",{}),"mtp",false) ? page.val(page.val(page.selectedResult(),"config",{}),"draft",1) : 0); color: Theme.text; font.pixelSize: 16; font.bold: true; Layout.fillWidth: true; elide: Text.ElideRight }
                            Text { text: page.fmt(page.val(page.selectedResult(),"scenario_seconds",null))+" с"; color: Theme.text; font.pixelSize: 21; font.bold: true }
                            Text { text: page.val(page.selectedResult(),"benchmark_label","Агентский сценарий"); color: Theme.muted; font.pixelSize: 12 }
                            Text { text: "VRAM                   "+page.fmt(page.val(page.selectedResult(),"vram_gb",null))+" ГБ"; color: Theme.muted; font.pixelSize: 12 }
                            Text { text: "Длинный вход     "+(page.longVerified(page.selectedResult()) ? "Проверен" : "Не проверен"); color: page.longVerified(page.selectedResult()) ? Theme.green : Theme.muted; font.pixelSize: 12 }
                            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }
                            Text { text: page.val(page.selectedResult(),"benchmark_label","")==="Архивный замер" ? "Архивный замер — вне новых рекомендаций" : (page.val(page.selectedResult(),"speed_status","")==="history" || page.val(page.selectedResult(),"reused",false) ? "Сценарий из истории" : "Новый замер сценария")+"\n"+(page.val(page.selectedResult(),"speed_created_at","") || page.val(page.selectedResult(),"created_at","")); color: Theme.muted; font.pixelSize: 11; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            Text { visible: !!page.val(page.selectedResult(),"long_result_id",""); text: "Длинный вход: "+page.val(page.selectedResult(),"long_created_at","сохранённый замер"); color: Theme.muted; font.pixelSize: 11; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            Text { text: page.val(page.val(page.selectedResult(),"config",{}),"runtime_name",page.val(page.draft,"runtime_id","")); color: Theme.muted; font.pixelSize: 11; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            Text { visible: page.val(page.selectedResult(),"status","completed")!=="completed" || !!page.val(page.selectedResult(),"error",""); text: page.val(page.selectedResult(),"error",page.val(page.selectedResult(),"status","")); color: Theme.red; font.pixelSize: 11; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            Item { Layout.fillHeight: true }
                            StudioButton { Layout.fillWidth: true; text: "Использовать для запуска"; iconName: "player-play"; primary: true; enabled: !!page.val(page.selectedResult(),"id","") && page.val(page.selectedResult(),"status","completed")==="completed"; onClicked: bridge.applyExperimentResult(page.val(page.selectedResult(),"id","")) }
                            StudioButton { Layout.fillWidth: true; text: "Создать похожее исследование"; iconName: "copy"; onClicked: { bridge.cloneExperiment(); page.view="builder" } }
                            StudioButton { Layout.fillWidth: true; text: "Открыть папку отчётов"; iconName: "folder"; subtle: true; onClicked: bridge.openReports() }
                        }
                    }
                }
            }
            Text { visible: page.val(page.preview,"status","")==="error" && page.view==="builder"; text: page.val(page.preview,"error",""); color: Theme.red; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        }
    }
    Dialog { id: conditionsDialog; objectName: "researchConditionsDialog"; title: "Общие условия исследования"; modal: true; anchors.centerIn: parent; width: Math.min(480,page.width-40); standardButtons: Dialog.Close
        background: Rectangle { color: Theme.panel2; border.color: Theme.border; radius: 7 }
        contentItem: ColumnLayout { spacing: 8
            Text { text: "Эти параметры одинаковы для всех сочетаний."; color: Theme.muted; font.pixelSize: 12 }
            Text { text: "Среда выполнения"; color: Theme.text; font.pixelSize: 12 }
            ComboBox { Layout.fillWidth: true; model: page.val(page.draft,"runtime_options",[]); textRole: "text"; valueRole: "value"; currentIndex: Math.max(0,indexOfValue(page.val(page.draft,"runtime_id",""))); onActivated: bridge.setResearchOption("runtime_id",currentValue) }
            RowLayout { Layout.fillWidth: true
                Text { text: "Рассуждение"; color: Theme.muted; font.pixelSize: 12; Layout.fillWidth: true }
                ComboBox { id: reasoningBox; model: [{text:"Off",mode:"off",budget:0},{text:"2K",mode:"on",budget:2048},{text:"4K",mode:"on",budget:4096},{text:"8K",mode:"on",budget:8192},{text:"Auto",mode:"auto",budget:0},{text:"On · без лимита",mode:"on",budget:0}]; textRole: "text"; currentIndex: page.reasoningIndex()
                    delegate: ItemDelegate { required property var modelData; required property int index; width: reasoningBox.width; text: modelData.text; enabled: page.reasoningAllowed(modelData); contentItem: Text { text: modelData.text; color: enabled ? Theme.text : Theme.faint; font.pixelSize: 12 } }
                    onActivated: { let o=model[index]; if (page.reasoningAllowed(o)) { bridge.setResearchOption("reasoning",o.mode); if (o.budget) bridge.setResearchOption("reasoning_budget",o.budget) } else currentIndex=Qt.binding(function(){ return page.reasoningIndex() }) }
                }
                ResearchCheck { text: "Изображения"; checked: !!page.val(page.val(page.draft,"config",{}),"mmproj",""); enabled: page.val(page.draft,"vision_available",false); onClicked: bridge.setResearchOption("vision",checked) }
            }
            RowLayout { Text { text: "Слои GPU (99 = Auto)"; color: Theme.muted; font.pixelSize: 12; Layout.fillWidth: true } SpinBox { from: 0; to: 999; value: Number(page.val(page.val(page.draft,"config",{}),"gpu_layers",99)); onValueModified: bridge.setResearchOption("gpu_layers",value) } }
            RowLayout { Text { text: "Повторы"; color: Theme.muted; font.pixelSize: 12; Layout.fillWidth: true } SpinBox { from: 1; to: 10; value: Number(page.val(page.draft,"runs",1)); onValueModified: bridge.setResearchOption("runs",value) } }
            StudioButton { Layout.fillWidth: true; text: "Копировать настройки запуска"; iconName: "copy"; onClicked: bridge.copyLaunchToResearch() }
        }
    }
    Dialog { id: stopDialog; title: "Остановить исследование?"; modal: true; anchors.centerIn: parent; standardButtons: Dialog.Yes | Dialog.Cancel; onAccepted: bridge.cancelResearch()
        contentItem: Text { text: "Завершённые замеры останутся в истории. Задание можно продолжить позже."; color: Theme.text; wrapMode: Text.WordWrap }
        background: Rectangle { color: Theme.panel2; border.color: Theme.border; radius: 6 }
    }
    Dialog { id: jobsDialog; title: "Задания исследования"; modal: true; anchors.centerIn: parent; width: Math.min(620,page.width-40); height: Math.min(460,page.height-40); standardButtons: Dialog.Close
        background: Rectangle { color: Theme.panel2; border.color: Theme.border; radius: 6 }
        contentItem: ListView { clip: true; model: bridge.experimentJobs || []
            delegate: Rectangle { required property var modelData; width: ListView.view.width; height: 58; color: "transparent"; border.color: Theme.border
                RowLayout { anchors.fill: parent; anchors.margins: 7
                    ColumnLayout { Layout.fillWidth: true; spacing: 2
                        Text { text: page.val(modelData,"created_at",page.val(modelData,"id","Задание")); color: Theme.text; font.pixelSize: 12 }
                        Text { text: page.val(modelData,"status","")+"  •  "+page.val(page.val(modelData,"summary",{}),"completed",page.val(modelData,"completed",0))+"/"+page.val(page.val(modelData,"summary",{}),"total",page.val(modelData,"total",0))+(page.val(modelData,"errors_count",0)>0 ? "  •  ошибок: "+page.val(modelData,"errors_count",0) : ""); color: page.val(modelData,"errors_count",0)>0 ? Theme.red : Theme.muted; font.pixelSize: 11 }
                    }
                    StudioButton { text: "Результаты"; buttonHeight: 28; onClicked: { bridge.setExperimentScope(page.val(modelData,"id","model")); page.showResults(); jobsDialog.close() } }
                    StudioButton { visible: ["cancelled","stopped","interrupted","failed"].indexOf(page.val(modelData,"status",""))>=0; text: "Продолжить"; iconName: "refresh"; buttonHeight: 28; onClicked: { bridge.resumeResearch(page.val(modelData,"id","")); jobsDialog.close() } }
                }
            }
        }
    }
}
