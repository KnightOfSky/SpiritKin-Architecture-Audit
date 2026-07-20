import SwiftUI

struct RuntimeHostView: View {
    @EnvironmentObject private var store: TerminalStore
    @State private var electionConfirmationPresented = false
    @State private var migrationConfirmationPresented = false
    @State private var selectedCheckpointID = ""
    @State private var selectedTargetHostID = ""

    private var registry: DynamicJSON { store.runtimeHostSnapshot["registry"] }
    private var hosts: [DynamicJSON] { registry["hosts"].arrayValue }
    private var leases: [DynamicJSON] { registry["leases"].arrayValue }
    private var checkpoints: [DynamicJSON] { store.runtimeHostSnapshot["checkpoints"]["checkpoints"].arrayValue }
    private var executionHosts: [DynamicJSON] {
        hosts.filter {
            $0["can_execute_workflows"].boolValue &&
            $0["effective_status"].stringValue == "online" &&
            !["fenced", "error", "not_reported"].contains($0["execution"]["status"].stringValue) &&
            $0["host_id"].stringValue != leases.first?["host_id"].stringValue
        }
    }

    var body: some View {
        List {
            Section("执行租约") {
                if let lease = leases.first, !lease["host_id"].stringValue.isEmpty {
                    RowLine(
                        title: lease["host_id"].stringValue,
                        subtitle: "epoch \(lease["epoch"].intValue) · 到期 \(lease["lease_expires_at"].stringValue)",
                        status: lease["effective_status"].stringValue
                    )
                } else {
                    Text("当前没有执行主机")
                        .foregroundStyle(.secondary)
                }
                Button("请求选主", systemImage: "arrow.triangle.2.circlepath") {
                    electionConfirmationPresented = true
                }
                .disabled(hosts.allSatisfy { !$0["can_execute_workflows"].boolValue })
            }

            Section("Runtime Hosts") {
                if hosts.isEmpty {
                    Text("暂无已注册主机")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(hosts.indices, id: \.self) { index in
                        let host = hosts[index]
                        let execution = host["execution"]["status"].stringValue
                        RowLine(
                            title: host["label"].stringValue.isEmpty ? host["host_id"].stringValue : host["label"].stringValue,
                            subtitle: "\(host["host_type"].stringValue) · \(host["capabilities"].arrayValue.count) 项能力 · \(host["can_execute_workflows"].boolValue ? "执行器 \(execution.isEmpty ? "未报告" : execution)" : "控制/观察")",
                            status: host["effective_status"].stringValue
                        )
                    }
                }
                Button("登记本机控制适配器", systemImage: "iphone.and.arrow.forward") {
                    Task {
                        await store.runtimeHostAction(
                            ["action": .string("register")],
                            successMessage: "iOS Runtime Host 已登记"
                        )
                    }
                }
            }

            Section("Checkpoint 迁移") {
                if checkpoints.isEmpty {
                    Text("暂无可迁移 Checkpoint")
                        .foregroundStyle(.secondary)
                } else {
                    Picker("Checkpoint", selection: $selectedCheckpointID) {
                        Text("选择 Checkpoint").tag("")
                        ForEach(checkpoints.indices, id: \.self) { index in
                            let item = checkpoints[index]
                            Text("\(item["workflow_name"].stringValue) · #\(item["sequence"].intValue)")
                                .tag(item["checkpoint_id"].stringValue)
                        }
                    }
                    Picker("目标主机", selection: $selectedTargetHostID) {
                        Text("选择目标主机").tag("")
                        ForEach(executionHosts.indices, id: \.self) { index in
                            Text(executionHosts[index]["host_id"].stringValue)
                                .tag(executionHosts[index]["host_id"].stringValue)
                        }
                    }
                    Button("请求迁移", systemImage: "arrow.right.arrow.left") {
                        migrationConfirmationPresented = true
                    }
                    .disabled(selectedCheckpointID.isEmpty || selectedTargetHostID.isEmpty)
                }
            }
        }
        .fantasyCanvas()
        .navigationTitle("Runtime Host")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            Button {
                Task { await store.refreshRuntimeHosts(force: true) }
            } label: {
                Image(systemName: "arrow.clockwise")
            }
            .accessibilityLabel("刷新 Runtime Host")
        }
        .task {
            await store.runtimeHostAction(["action": .string("register")], successMessage: "Runtime Host 已连接")
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(20))
                guard !Task.isCancelled else { return }
                await store.runtimeHostAction(["action": .string("heartbeat")], successMessage: "Runtime Host 心跳正常")
            }
        }
        .confirmationDialog("请求 Runtime Host 重新选主？", isPresented: $electionConfirmationPresented, titleVisibility: .visible) {
            Button("确认选主") {
                Task {
                    await store.runtimeHostAction(
                        ["action": .string("request_election"), "confirmed": .bool(true)],
                        successMessage: "Runtime Host 选主已完成"
                    )
                }
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("现有有效租约不会被强制抢占；只有租约过期或主机失效时才会更换执行主机。")
        }
        .confirmationDialog("请求迁移这个 Workflow？", isPresented: $migrationConfirmationPresented, titleVisibility: .visible) {
            Button("确认迁移") {
                Task {
                    await store.runtimeHostAction(
                        [
                            "action": .string("request_migration"),
                            "checkpoint_id": .string(selectedCheckpointID),
                            "target_host_id": .string(selectedTargetHostID),
                            "confirmed": .bool(true)
                        ],
                        successMessage: "迁移请求已提交给目标 Runtime Host"
                    )
                }
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("目标主机将从 Checkpoint 继续；执行中的副作用节点会先进入对账审核，不会自动重放。")
        }
        .safeAreaInset(edge: .bottom) { StatusBar(text: store.statusMessage) }
    }
}
