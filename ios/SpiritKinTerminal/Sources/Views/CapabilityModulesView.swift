import SwiftUI

struct CapabilityModulesView: View {
    @EnvironmentObject private var store: TerminalStore

    var body: some View {
        Form {
            Section("能力模块") {
                ForEach(store.capabilitySnapshot.capabilities) { item in
                    Toggle(isOn: Binding(
                        get: { item.enabled },
                        set: { enabled in Task { await store.toggleCapability(item, enabled: enabled) } }
                    )) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(item.label)
                            Text(item.detail)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .disabled(item.locked)
                }
            }
            Section("说明") {
                Text("安全控制始终保持开启。关闭模块会由主控端执行入口拒绝对应请求，而不是只隐藏界面。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .fantasyCanvas()
        .navigationTitle("能力模块")
        .task { await store.refreshCapabilities() }
    }
}
