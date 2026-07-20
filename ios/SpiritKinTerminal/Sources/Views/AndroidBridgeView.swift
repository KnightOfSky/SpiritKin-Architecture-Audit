import SwiftUI

struct AndroidBridgeView: View {
    @EnvironmentObject private var store: TerminalStore
    @State private var deviceId = "android_device"
    @State private var operation = "app.launch"
    @State private var paramsJSON = #"{"app_name":"Feishu"}"#

    private let operations = [
        "app.launch",
        "url.open",
        "clipboard.write",
        "workflow.android_step.status",
        "workflow.android_step"
    ]

    var body: some View {
        NavigationStack {
            Form {
                Section("Runtime Host") {
                    NavigationLink {
                        RuntimeHostView()
                    } label: {
                        Label("主机、Checkpoint 与迁移", systemImage: "server.rack")
                    }
                    NavigationLink {
                        WorldObservationView()
                    } label: {
                        Label("World Observation", systemImage: "viewfinder")
                    }
                }
                Section("工作区与设备") {
                    if store.workspaceDeviceGroups.isEmpty {
                        Text("暂无工作区设备。先让手机端绑定到工作区。")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(store.workspaceDeviceGroups) { group in
                            VStack(alignment: .leading, spacing: 6) {
                                Text("\(group.workspaceID) · \(group.name.isEmpty ? group.workspaceID : group.name)")
                                    .font(.headline)
                                Text("手机端 \(group.androidCount) · iOS 主控 \(group.iosControllerCount) · 远程执行 \(group.remoteWorkerCount)")
                                    .font(.subheadline)
                                Text("绑定 \(group.activeBindingCount) · 待用配对码 \(group.pendingPairingCount) · 最近活动 \(group.lastSeenAt.isEmpty ? "--" : group.lastSeenAt)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                workspaceEntries("Android 手机端", group.androidDevices)
                                workspaceEntries("iOS 主控端", group.iosControllers)
                                workspaceEntries("远程执行端", group.remoteWorkers)
                            }
                            .padding(.vertical, 4)
                        }
                    }
                }
                Section("Remote Worker 配对") {
                    if store.remoteWorkerPairing["pairing_token"].stringValue.isEmpty {
                        Button("生成一次性配对码", systemImage: "link.badge.plus") {
                            Task { await store.createRemoteWorkerPairing() }
                        }
                        Text("配对码有效期为 10 分钟，只绑定当前 workspace；生成不会自动启动或授权 Worker 执行高风险任务。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    } else {
                        LabeledContent("工作区", value: store.remoteWorkerPairing["workspace_id"].stringValue)
                        Text(store.remoteWorkerPairing["pairing_token"].stringValue)
                            .font(.system(.footnote, design: .monospaced))
                            .textSelection(.enabled)
                        if !store.remoteWorkerPairing["pairing_command"].stringValue.isEmpty {
                            Text(store.remoteWorkerPairing["pairing_command"].stringValue)
                                .font(.system(.caption, design: .monospaced))
                                .textSelection(.enabled)
                            ShareLink(item: store.remoteWorkerPairing["pairing_command"].stringValue) {
                                Label("分享启动命令", systemImage: "square.and.arrow.up")
                            }
                        }
                        LabeledContent("有效期", value: store.remoteWorkerPairing["expires_at"].stringValue)
                        Button("取消配对码", role: .destructive) {
                            Task { await store.cancelRemoteWorkerPairing() }
                        }
                    }
                }
                Section("手动下发手机步骤") {
                    TextField("设备编号，默认当前工作区手机", text: $deviceId)
                    Picker("步骤", selection: $operation) {
                        ForEach(operations, id: \.self) { item in
                            Text(item).tag(item)
                        }
                    }
                    TextEditor(text: $paramsJSON)
                        .frame(minHeight: 88)
                        .font(.system(.footnote, design: .monospaced))
                    Button("下发步骤") {
                        Task { await queueCommand() }
                    }
                    Button("清空这个设备的待执行步骤", role: .destructive) {
                        Task {
                            await store.sendAction([
                                "action": .string("clear_android_commands"),
                                "device_id": .string(deviceId)
                            ], successMessage: "已清空手机端待执行步骤")
                        }
                    }
                }
                Section("本机队列心跳") {
                    if store.androidDevices.isEmpty {
                        Text("暂无手机端心跳")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(store.androidDevices) { device in
                            RowLine(
                                title: device.deviceId,
                                subtitle: "battery \(device.battery) · app \(device.currentApp) · pending \(device.pending) · running \(device.inflight)",
                                status: device.online ? "online" : "offline"
                            )
                        }
                    }
                }
                Section("最近手机步骤") {
                    ForEach(store.recentCommands) { command in
                        RowLine(
                            title: "\(command.deviceId) · \(command.operation)",
                            subtitle: command.message,
                            status: command.status
                        )
                    }
                }
            }
            .fantasyCanvas()
            .navigationTitle("设备与主机")
            .toolbar {
                Button {
                    Task { await store.refresh(force: true) }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .accessibilityLabel("刷新")
            }
            .safeAreaInset(edge: .bottom) {
                StatusBar(text: store.statusMessage)
            }
        }
    }

    private func queueCommand() async {
        do {
            let params = try JSONHelpers.parseObject(paramsJSON)
            await store.sendAction([
                "action": .string("enqueue_android_command"),
                "device_id": .string(deviceId),
                "operation": .string(operation),
                "params": .object(params)
            ], successMessage: "已下发手机步骤")
        } catch {
            store.statusMessage = error.localizedDescription
        }
    }

    @ViewBuilder
    private func workspaceEntries(_ title: String, _ entries: [WorkspaceDeviceEntry]) -> some View {
        if !entries.isEmpty {
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                ForEach(entries) { entry in
                    Text("\(entry.deviceID) · \(entry.status)\(entry.foregroundPackage.isEmpty ? "" : " · 前台 \(entry.foregroundPackage)")")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }
}
