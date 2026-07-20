import SwiftUI

struct ConversationPanel: View {
    @EnvironmentObject private var store: TerminalStore

    var body: some View {
        VStack(spacing: 10) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: 9) {
                        ForEach(store.conversationMessages.suffix(4)) { message in
                            ConversationBubble(message: message)
                                .id(message.id)
                        }
                    }
                }
                .frame(maxHeight: 152)
                .onChange(of: store.conversationMessages.count) { _, _ in
                    if let last = store.conversationMessages.last {
                        withAnimation(.easeOut(duration: 0.18)) {
                            proxy.scrollTo(last.id, anchor: .bottom)
                        }
                    }
                }
            }

            HStack(alignment: .bottom, spacing: 8) {
                TextField("向 SpiritKin 发消息", text: $store.conversationDraft, axis: .vertical)
                    .lineLimit(1...4)
                    .textFieldStyle(.plain)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
                    .background(FantasyTheme.surface2, in: RoundedRectangle(cornerRadius: 8))
                    .overlay(RoundedRectangle(cornerRadius: 8).stroke(FantasyTheme.line))
                    .onSubmit {
                        Task { await store.sendConversationMessage() }
                    }

                Button {
                    Task { await store.sendConversationMessage() }
                } label: {
                    Image(systemName: "arrow.up")
                        .font(.body.weight(.bold))
                        .frame(width: 42, height: 42)
                }
                .buttonStyle(.borderedProminent)
                .disabled(store.conversationDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || store.isLoading)
                .accessibilityLabel("发送")
            }
        }
        .padding(12)
        .background(FantasyTheme.surface)
    }
}

private struct ConversationBubble: View {
    let message: TerminalConversationMessage

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            if message.role == .user {
                Spacer(minLength: 54)
            } else {
                Image(systemName: message.role == .system ? "exclamationmark.triangle" : "sparkles")
                    .foregroundStyle(message.role == .system ? FantasyTheme.warning : FantasyTheme.primary)
                    .frame(width: 22)
            }

            Text(message.text)
                .font(.subheadline)
                .foregroundStyle(message.role == .user ? FantasyTheme.onPrimary : FantasyTheme.text)
                .padding(.horizontal, message.role == .user ? 12 : 0)
                .padding(.vertical, message.role == .user ? 9 : 2)
                .background {
                    if message.role == .user {
                        RoundedRectangle(cornerRadius: 8).fill(FantasyTheme.primary)
                    }
                }
                .frame(maxWidth: .infinity, alignment: message.role == .user ? .trailing : .leading)

            if message.role != .user {
                Spacer(minLength: 28)
            }
        }
    }
}
