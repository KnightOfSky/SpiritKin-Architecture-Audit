import SwiftUI

struct IOSAutomationView: View {
    @EnvironmentObject private var store: TerminalStore

    var body: some View {
        List {
            Section("快捷指令目录") {
                if store.shortcutCatalog.isEmpty {
                    ContentUnavailableView(
                        "目录尚未同步",
                        systemImage: "command",
                        description: Text("连接主控后读取 /ios/schemas/shortcuts.json。")
                    )
                } else {
                    ForEach(store.shortcutCatalog.indices, id: \.self) { index in
                        let item = store.shortcutCatalog[index]
                        AutomationRow(
                            title: item["name"].stringValue,
                            detail: item["description"].stringValue,
                            status: item["confirmation_required"].boolValue ? "需确认" : "已定义"
                        )
                    }
                }
            }

            Section("原生 App Intents") {
                AutomationRow(title: "Ask Spirit", detail: "从 Siri、Spotlight 或快捷指令提交问题", status: "已注册")
                AutomationRow(title: "Check Spirit Status", detail: "读取当前主控服务健康状态", status: "已注册")
                AutomationRow(title: "Read Clipboard", detail: "用户主动运行后读取当前剪贴板", status: "已注册")
                AutomationRow(title: "Write Clipboard", detail: "把快捷指令文本写入剪贴板", status: "已注册")
                AutomationRow(title: "Send Notification", detail: "在当前 iPhone 投递本地通知", status: "已注册")
                AutomationRow(title: "Check Battery", detail: "读取本机电量与充电状态", status: "已注册")
            }

            Section("跨 App 接入") {
                AutomationRow(title: "照片与文件", detail: "系统照片选择器与文件选择器", status: "可用")
                AutomationRow(title: "Share Extension", detail: "接收其他 App 分享的图片与文件，主 App 启动后上传", status: "已注册")
                AutomationRow(title: "URL Scheme", detail: "打开提供公开 Scheme 的 App", status: "按 App")
                AutomationRow(title: "目标 App Intents", detail: "调用目标 App 明确暴露的动作", status: "按 App")
                AutomationRow(title: "公开 API", detail: "经主控端权限与审计层调用", status: "受控")
            }

            Section("微信 iLink") {
                let channel = store.channelsSnapshot["wechat_ilink"]
                AutomationRow(
                    title: "Runtime 集中通道",
                    detail: channel["message"].stringValue.isEmpty ? "由桌面 Runtime 统一收发，iOS 不保存 Bot Token" : channel["message"].stringValue,
                    status: channel["phase"].stringValue.isEmpty ? "未配置" : channel["phase"].stringValue
                )
                if !channel["bot_id"].stringValue.isEmpty {
                    LabeledContent("Bot", value: channel["bot_id"].stringValue)
                }
            }

            Section("接口") {
                Text("POST /ios/shortcut")
                Text("POST /ios/intent")
                Text("GET /ios/schemas/shortcuts.json")
            }

            Section {
                Text("iOS 不允许应用任意点击或读取其他 App。SpiritKin 只使用系统公开的自动化和数据交换能力，高风险动作仍需确认。")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .fantasyCanvas()
        .navigationTitle("iOS 自动化")
        .toolbar {
            Button { Task { await store.refreshShortcutCatalog(force: true) } } label: {
                Image(systemName: "arrow.clockwise")
            }
            .accessibilityLabel("刷新快捷指令目录")
        }
        .task {
            async let shortcuts: Void = store.refreshShortcutCatalog()
            async let channels: Void = store.refreshChannels(force: true)
            _ = await (shortcuts, channels)
        }
    }
}

private struct AutomationRow: View {
    let title: String
    let detail: String
    let status: String

    private var statusColor: Color {
        ["已定义", "已注册", "可用"].contains(status) ? FantasyTheme.success : FantasyTheme.warning
    }

    var body: some View {
        LabeledContent {
            Text(status)
                .font(.caption.weight(.medium))
                .foregroundStyle(statusColor)
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }
}
