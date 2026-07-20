import SwiftUI

struct ConversationSessionsView: View {
    @EnvironmentObject private var store: TerminalStore
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                if store.conversationSessions.isEmpty {
                    ContentUnavailableView("暂无会话", systemImage: "message", description: Text("新建会话后可在 iOS 与桌面端同步。"))
                } else {
                    ForEach(store.conversationSessions.sorted(by: { $0.updatedAt > $1.updatedAt })) { session in
                        Button {
                            Task {
                                await store.selectConversation(session.id)
                                dismiss()
                            }
                        } label: {
                            HStack(spacing: 12) {
                                Image(systemName: session.id == store.activeConversationSessionID ? "message.fill" : "message")
                                    .foregroundStyle(session.id == store.activeConversationSessionID ? FantasyTheme.primary : FantasyTheme.muted)
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(session.title)
                                        .foregroundStyle(FantasyTheme.text)
                                        .lineLimit(1)
                                    Text("\(session.messages.count) 条消息 · \(session.status == "archived" ? "已归档" : "进行中")")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                if session.id == store.activeConversationSessionID {
                                    Image(systemName: "checkmark")
                                        .foregroundStyle(FantasyTheme.primary)
                                }
                            }
                        }
                        .swipeActions(edge: .trailing) {
                            Button(role: .destructive) {
                                Task { await store.deleteConversation(session.id) }
                            } label: {
                                Label("删除", systemImage: "trash")
                            }
                            Button {
                                Task { await store.archiveConversation(session.id) }
                            } label: {
                                Label("归档", systemImage: "archivebox")
                            }
                            .tint(FantasyTheme.secondary)
                        }
                    }
                }
            }
            .fantasyCanvas()
            .navigationTitle("会话")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("关闭") { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        store.createConversation()
                        dismiss()
                    } label: {
                        Image(systemName: "square.and.pencil")
                    }
                    .accessibilityLabel("新建会话")
                }
            }
        }
    }
}
