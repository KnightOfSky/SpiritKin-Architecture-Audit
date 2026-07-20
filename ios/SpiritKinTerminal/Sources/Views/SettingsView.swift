import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var store: TerminalStore
    @StateObject private var notifications = NotificationManager()
    @State private var configText = ""
    @State private var pairingToken = ""
    @AppStorage("spiritkin.appearance.mode") private var appearanceMode = SpiritKinAppearance.system.rawValue

    var body: some View {
        NavigationStack {
            Form {
                Section("外观") {
                    Picker("显示模式", selection: $appearanceMode) {
                        ForEach(SpiritKinAppearance.allCases) { mode in
                            Text(mode.title).tag(mode.rawValue)
                        }
                    }
                    .pickerStyle(.segmented)
                    Text("与桌面端、Web 和 Android Bridge 共用 v4 日夜语义色。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("Avatar") {
                    Text("头像请在“我的”中从照片选择；此处仅保留开发者地址覆盖。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    TextField("开发者头像 URL（可留空）", text: $store.customAvatarURL)
                        .textInputAutocapitalization(.never)
                        .keyboardType(.URL)
                    Text("远程地址只接受 HTTPS；照片头像保存在本机应用目录。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("连接主控") {
                    TextField("主控地址", text: $store.baseURLString)
                        .textInputAutocapitalization(.never)
                        .keyboardType(.URL)
                    SecureField("iOS 访问令牌", text: $store.token)
                        .textInputAutocapitalization(.never)
                    SecureField("一次性 iOS 配对码", text: $pairingToken)
                        .textInputAutocapitalization(.never)
                    Button("绑定 iOS 主控") {
                        Task {
                            await store.bindPairingToken(pairingToken)
                            pairingToken = ""
                        }
                    }
                    .disabled(pairingToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    Text("在桌面端移动管理中生成 device_role=iOS terminal 的配对码；绑定后会自动换取工作区访问令牌。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Button("测试连接") {
                        Task { await store.refresh(force: true) }
                    }
                    Button("诊断 iOS 健康状态") {
                        Task { await store.diagnoseConnection() }
                    }
                    if !store.workspaceID.isEmpty {
                        Text("工作区：\(store.workspaceID)")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
                Section("导入配置") {
                    TextEditor(text: $configText)
                        .frame(minHeight: 88)
                        .font(.system(.footnote, design: .monospaced))
                    Button("应用配置") {
                        store.applyConfig(from: configText)
                    }
                    Text("支持一键配置链接、主控地址，或包含 base_url/token/workspace_id 的 JSON。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("通知") {
                    RowLine(title: "通知授权", subtitle: "本机提醒主控事件", status: notifications.authorizationStatus)
                    Button("请求通知权限") {
                        Task { await notifications.requestAuthorization() }
                    }
                    Button("发送本地测试通知") {
                        notifications.sendLocalTest()
                    }
                }
                Section("后台刷新") {
                    RowLine(title: "后台刷新任务", subtitle: "com.spiritkin.terminal.refresh", status: "已配置")
                    Button("安排后台刷新") {
                        BackgroundRefresh.schedule()
                        store.statusMessage = "已安排后台刷新"
                    }
                }
                Section("接口") {
                    Text("GET /ios/native/snapshot")
                    Text("POST /ios/native/action")
                    Text("POST /mobile/artifacts")
                }
                Section("iOS 自动化") {
                    NavigationLink("快捷指令与 App 接入") {
                        IOSAutomationView()
                    }
                    Text("通过 Shortcuts、App Intents、URL Scheme、系统文件选择器和目标 App 公开接口连接，不绕过 iOS 沙盒。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("状态") {
                    Text(store.statusMessage)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                Section("调试") {
                    NavigationLink("原始快照") {
                        RawJSONView(title: "快照", value: .object([
                            "services": store.snapshot.services,
                            "safety": store.snapshot.safety,
                            "mobile_management": store.snapshot.mobileManagement,
                            "workflows": store.snapshot.workflows,
                            "module_management": store.snapshot.moduleManagement,
                            "model_governance": store.snapshot.modelGovernance,
                            "snapshot_meta": store.snapshot.snapshotMeta
                        ]))
                    }
                }
            }
            .fantasyCanvas()
            .navigationTitle("设置")
            .task {
                await notifications.refreshStatus()
            }
        }
    }
}
