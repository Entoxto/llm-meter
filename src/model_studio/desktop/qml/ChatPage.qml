import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

Item {
    id: page
    property var bridge
    property var host
    property string searchText: ""
    property string selectedConversationId: ""
    function v(o,k,d) { let x=o && o[k]; return x===undefined || x===null || x==="" ? d : x }
    function displayText(m) {
        let t=v(m,"text",""); if (t) return t
        let meta=v(m,"metadata",{}), reason=String(v(meta,"finish_reason",v(meta,"done_reason",v(m,"finish_reason","")))).toLowerCase()
        if (reason.indexOf("length")>=0 || reason.indexOf("limit")>=0 || reason.indexOf("token")>=0) return "Достигнут лимит ответа. Откройте рассуждение, если оно доступно, или отправьте новый запрос."
        let status=v(m,"status","")
        if (status==="streaming") return "Модель отвечает…"
        if (status==="cancelled" || status==="interrupted") return "Ответ остановлен."
        return "Пустой ответ модели."
    }
    function safeMarkdown(s) { return String(s).replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/!\[/g,"[") }
    function responding() { let a=bridge.messages || []; return !!bridge.busy && a.length>0 && v(a[a.length-1],"role","")==="assistant" && v(a[a.length-1],"status","")==="streaming" }
    Connections { target: bridge; function onMessageAccepted() { compose.text="" } }
    RowLayout { anchors.fill: parent; spacing: 0
        Rectangle { Layout.preferredWidth: 220; Layout.fillHeight: true; color: Theme.panel; border.color: Theme.border
            ColumnLayout { anchors.fill: parent; anchors.margins: 10; spacing: 10
                StudioButton { text: "Новый чат"; iconName: "plus"; primary: true; Layout.fillWidth: true; onClicked: { page.selectedConversationId=""; bridge.newChat() } }
                StudioField { Layout.fillWidth: true; placeholderText: "Поиск в загруженных чатах…"; onTextChanged: page.searchText=text.toLowerCase() }
                ListView { Layout.fillHeight: true; Layout.fillWidth: true; clip: true; model: bridge.conversations || []
                    delegate: Rectangle { required property var modelData; width: ListView.view.width; height: 60; radius: 6
                        visible: String(page.v(modelData,"title","")).toLowerCase().indexOf(page.searchText)>=0
                        color: page.selectedConversationId===page.v(modelData,"id","") ? "#202648" : "transparent"
                        MouseArea { anchors.fill: parent; onClicked: { page.selectedConversationId=page.v(modelData,"id",""); bridge.selectConversation(page.selectedConversationId) } }
                        RowLayout { anchors.fill: parent; anchors.margins: 9; spacing: 7
                            StudioIcon { name: "message"; width: 16; height: 16 }
                            ColumnLayout { Layout.fillWidth: true
                                Text { text: page.v(modelData,"title","Новый чат"); color: Theme.text; font.pixelSize: 13; elide: Text.ElideRight; Layout.fillWidth: true }
                                Text { text: page.v(modelData,"preview",""); color: Theme.muted; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
                            }
                            StudioButton { text: ""; iconName: "trash"; buttonHeight: 26; implicitWidth: 27; subtle: true; onClicked: { deleteDialog.conversationId=page.v(modelData,"id",""); deleteDialog.open() } }
                        }
                    }
                }
                StudioButton { visible: !!bridge.hasMoreConversations; text: "Загрузить ещё чаты"; iconName: "chevron-down"; Layout.fillWidth: true; onClicked: bridge.loadMoreConversations() }
            }
        }
        ColumnLayout { Layout.fillHeight: true; Layout.fillWidth: true; spacing: 0
            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 64; color: Theme.bg; border.color: Theme.border
                RowLayout { anchors.fill: parent; anchors.margins: 14; spacing: 13
                    StudioIcon { name: "cube"; width: 23; height: 23 }
                    ColumnLayout { Layout.fillWidth: true; spacing: 3
                        RowLayout { Layout.fillWidth: true; Text { Layout.fillWidth: true; elide: Text.ElideRight; text: host.modelName(); color: Theme.text; font.pixelSize: 15 } Text { text: "●  " + host.statusText(); color: host.statusColor(); font.pixelSize: 12 } }
                        Text { text: "Контекст: " + host.contextText() + (page.v(bridge.session,"vision_available",false) ? " · Изображения доступны" : " · Только текст"); color: Theme.muted; font.pixelSize: 12 }
                    }
                    StudioButton { text: "Настройки модели"; iconName: "settings"; subtle: true; onClicked: host.navigate(0) }
                }
            }
            Item { Layout.fillHeight: true; Layout.fillWidth: true
                ListView { id: messages; anchors.fill: parent; anchors.margins: 16; clip: true; spacing: 14; model: bridge.messages || []
                    onCountChanged: positionViewAtEnd()
                    delegate: Item { required property var modelData; width: ListView.view.width; height: bubble.implicitHeight + 13
                        property bool fromUser: page.v(modelData,"role","")==="user"
                        StudioCard { id: bubble; width: Math.min(parent.width*0.88, 900); implicitHeight: messageColumn.implicitHeight + 30; anchors.right: fromUser ? parent.right : undefined; anchors.left: fromUser ? undefined : parent.left; color: fromUser ? "#24254d" : Theme.panel2
                            ColumnLayout { id: messageColumn; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12; spacing: 6
                                Flow { Layout.fillWidth: true; spacing: 6; visible: (page.v(modelData,"images",[]) || []).length>0
                                    Repeater { model: page.v(modelData,"images",[])
                                        ImageAttachment { required property var modelData; attachment: modelData }
                                    }
                                }
                                TextEdit { text: fromUser ? page.v(modelData,"text","") : page.safeMarkdown(page.displayText(modelData)); textFormat: fromUser ? TextEdit.PlainText : TextEdit.MarkdownText; readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap; color: Theme.text; font.pixelSize: 14; font.family: "Segoe UI"; Layout.fillWidth: true; onLinkActivated: function(link) {} }
                                Rectangle { visible: !!page.v(modelData,"reasoning",""); Layout.fillWidth: true; implicitHeight: reasoningColumn.implicitHeight+12; color: Theme.panel; radius: 5; border.color: Theme.border
                                    property bool expanded: false
                                    Column { id: reasoningColumn; width: parent.width-16; anchors.top: parent.top; anchors.left: parent.left; anchors.margins: 6; spacing: 6
                                        Text { text: "Рассуждение  •  " + (parent.parent.expanded ? "скрыть" : "показать"); color: Theme.muted; font.pixelSize: 12
                                            MouseArea { anchors.fill: parent; onClicked: parent.parent.parent.expanded=!parent.parent.parent.expanded }
                                        }
                                        TextEdit { visible: parent.parent.expanded; width: parent.width; text: page.v(modelData,"reasoning",""); readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap; color: Theme.muted; font.pixelSize: 12 }
                                    }
                                }
                                RowLayout { visible: !fromUser; Layout.fillWidth: true
                                    StudioButton { text: "Скопировать"; iconName: "copy"; subtle: true; buttonHeight: 22; enabled: !!page.v(modelData,"text",""); onClicked: bridge.copyText(page.v(modelData,"text","")) }
                                    Item { Layout.fillWidth: true }
                                    Text { text: page.v(page.v(modelData,"metadata",{}),"tokens_per_second",null)!==null ? "Этот ответ: " + page.v(modelData.metadata,"tokens_per_second","") + " ток/с" : ""; color: Theme.muted; font.pixelSize: 12 }
                                }
                            }
                        }
                    }
                }
                ColumnLayout { anchors.centerIn: parent; visible: messages.count===0; spacing: 12
                    Text { text: host.running() ? "Новый разговор" : "Модель не запущена"; color: Theme.text; font.pixelSize: 22; font.bold: true; Layout.alignment: Qt.AlignHCenter }
                    Text { text: host.running() ? "Напишите сообщение, чтобы начать диалог." : "Выберите модель на экране запуска и запустите сервер."; color: Theme.muted; font.pixelSize: 14; Layout.alignment: Qt.AlignHCenter }
                    StudioButton { visible: !host.running(); text: "К запуску"; iconName: "player-play"; primary: true; Layout.alignment: Qt.AlignHCenter; onClicked: host.navigate(0) }
                }
            }
            Flow { Layout.fillWidth: true; Layout.leftMargin: 15; Layout.rightMargin: 15; spacing: 8; visible: (bridge.pendingImages || []).length>0
                Repeater { model: bridge.pendingImages || []
                    ImageAttachment { required property var modelData; attachment: modelData; removable: !bridge.busy; onRemoveRequested: bridge.removeChatImage(modelData.id) }
                }
            }
            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 75; color: Theme.bg
                RowLayout { anchors.fill: parent; anchors.margins: 15; spacing: 10
                    StudioButton { text: "Картинка"; iconName: "plus"; enabled: host.running() && !bridge.busy; onClicked: bridge.addChatImages() }
                    StudioField { id: compose; Layout.fillWidth: true; Layout.fillHeight: true; placeholderText: host.running() ? "Напишите сообщение…" : "Запустите модель, чтобы отправить сообщение"; enabled: host.running() && !bridge.busy; onAccepted: send()
                        function send() { let t=text.trim(); if(t || (bridge.pendingImages || []).length) bridge.sendMessage(t) }
                    }
                    StudioButton { text: page.responding() ? "Стоп" : bridge.busy ? "Занято" : "Отправить"; iconName: page.responding() ? "x" : "send"; primary: !bridge.busy; danger: page.responding(); enabled: page.responding() || (host.running() && !bridge.busy); onClicked: page.responding() ? bridge.cancel() : compose.send() }
                }
            }
        }
    }
    Dialog { id: deleteDialog; property string conversationId: ""; title: "Удалить диалог?"; modal: true; anchors.centerIn: parent; standardButtons: Dialog.Yes | Dialog.Cancel; onAccepted: bridge.deleteConversation(conversationId)
        contentItem: Text { text: "Диалог будет удалён из локальной истории."; color: Theme.text }
        background: Rectangle { color: Theme.panel2; border.color: Theme.border; radius: 6 }
    }
}
