import SwiftUI

struct RuntimeMonitorView: View {
    @EnvironmentObject private var store: TerminalStore
    @State private var pendingRetry: DynamicJSON?

    private var monitor: DynamicJSON { store.monitorSnapshot }
    private var incidents: [DynamicJSON] { monitor["incidents"].arrayValue }

    var body: some View {
        List {
            Section("当前 workspace") {
                RowLine(
                    title: monitor["workspace_id"].stringValue.isEmpty ? store.workspaceID : monitor["workspace_id"].stringValue,
                    subtitle: "事件 \(monitor["incident_count"].intValue) · 可自动修复 \(monitor["auto_repairable_count"].intValue)",
                    status: monitor["status"].stringValue
                )
                Button("修复可自动处理项", systemImage: "wrench.and.screwdriver") {
                    Task {
                        await store.monitorAction(["action": .string("auto_repair")], successMessage: "低风险监控项已处理")
                    }
                }
                .disabled(monitor["auto_repairable_count"].intValue == 0)
            }

            Section("事件") {
                if incidents.isEmpty {
                    Label("工作区、设备与 Remote Worker 正常", systemImage: "checkmark.circle")
                        .foregroundStyle(.green)
                } else {
                    ForEach(incidents.indices, id: \.self) { index in
                        let item = incidents[index]
                        RowLine(
                            title: item["title"].stringValue,
                            subtitle: item["detail"].stringValue,
                            status: item["severity"].stringValue
                        )
                        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                            if item["repair_action"].stringValue == "retry_workflow_run" {
                                Button("重试", systemImage: "arrow.clockwise") {
                                    pendingRetry = item
                                }
                                .tint(.orange)
                            }
                        }
                    }
                }
            }
        }
        .fantasyCanvas()
        .navigationTitle("运行监控")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            Button { Task { await store.refreshMonitor(force: true) } } label: { Image(systemName: "arrow.clockwise") }
                .accessibilityLabel("刷新运行监控")
        }
        .task { await store.refreshMonitor() }
        .confirmationDialog(
            "确认重试 Workflow？",
            isPresented: Binding(
                get: { pendingRetry != nil },
                set: { if !$0 { pendingRetry = nil } }
            ),
            titleVisibility: .visible
        ) {
            Button("确认重试") {
                guard let incident = pendingRetry else { return }
                pendingRetry = nil
                Task {
                    await store.monitorAction(
                        [
                            "action": .string("retry_workflow_run"),
                            "run_id": .string(incident["target_id"].stringValue)
                        ],
                        successMessage: "Workflow 已提交重试"
                    )
                }
            }
            Button("取消", role: .cancel) { pendingRetry = nil }
        } message: {
            Text("Workflow 可能包含外部副作用，重试前请确认失败步骤不会重复发布、扣费或写入数据。")
        }
        .safeAreaInset(edge: .bottom) { StatusBar(text: store.statusMessage) }
    }
}
