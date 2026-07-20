import SwiftUI

private struct ResourceEditor: Identifiable {
    let id = UUID()
    let existingID: String
    let initialLabel: String
    let initialType: String
    let initialPlatform: String
    let initialHealth: String
    let initialTags: String
    let initialCapabilities: String

    var isNew: Bool { existingID.isEmpty }
}

struct ResourceManagementView: View {
    @EnvironmentObject private var store: TerminalStore
    @State private var editor: ResourceEditor?

    private var resources: [DynamicJSON] {
        store.resourceSnapshot["resource_registry"]["resources"].arrayValue
    }

    var body: some View {
        List {
            Section {
                if resources.isEmpty {
                    Text("当前 workspace 暂无 Resource")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(resources.indices, id: \.self) { index in
                        resourceRow(resources[index])
                    }
                }
            } footer: {
                Text("全局 Resource 只读；移动端只能修改当前 workspace 的资源，凭据必须保存在 Keychain/Vault 并仅登记引用。")
            }
        }
        .fantasyCanvas()
        .navigationTitle("Resource 管理")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button {
                    editor = ResourceEditor(
                        existingID: "",
                        initialLabel: "",
                        initialType: "generic",
                        initialPlatform: "",
                        initialHealth: "unknown",
                        initialTags: "",
                        initialCapabilities: ""
                    )
                } label: {
                    Image(systemName: "plus")
                }
                .accessibilityLabel("新增 Resource")
                Button { Task { await store.refreshResources(force: true) } } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .accessibilityLabel("刷新 Resource")
            }
        }
        .sheet(item: $editor) { item in
            ResourceEditorSheet(editor: item) { payload in
                await store.resourceAction(payload, successMessage: "Resource 已更新")
            }
        }
        .task { await store.refreshResources() }
        .safeAreaInset(edge: .bottom) { StatusBar(text: store.statusMessage) }
    }

    @ViewBuilder
    private func resourceRow(_ item: DynamicJSON) -> some View {
        let resourceID = item["resource_id"].stringValue
        let editable = item["editable"].boolValue
        let row = RowLine(
            title: item["label"].stringValue.isEmpty ? resourceID : item["label"].stringValue,
            subtitle: "\(item["resource_type"].stringValue) · \(item["platform"].stringValue)",
            status: item["health_status"].stringValue
        )
        if editable {
            Button {
                editor = ResourceEditor(
                    existingID: resourceID,
                    initialLabel: item["label"].stringValue,
                    initialType: item["resource_type"].stringValue,
                    initialPlatform: item["platform"].stringValue,
                    initialHealth: item["health_status"].stringValue,
                    initialTags: item["tags"].arrayValue.map(\.stringValue).joined(separator: ", "),
                    initialCapabilities: item["supported_capabilities"].arrayValue.map(\.stringValue).joined(separator: ", ")
                )
            } label: { row }
            .buttonStyle(.plain)
            .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                if item["deletable"].boolValue {
                    Button(role: .destructive) {
                        Task {
                            await store.resourceAction(
                                ["action": .string("delete"), "resource_id": .string(resourceID)],
                                successMessage: "Resource 已删除"
                            )
                        }
                    } label: {
                        Label("删除", systemImage: "trash")
                    }
                }
            }
        } else {
            row
        }
    }
}

private struct ResourceEditorSheet: View {
    let editor: ResourceEditor
    let onSave: ([String: DynamicJSON]) async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var resourceID: String
    @State private var label: String
    @State private var resourceType: String
    @State private var platform: String
    @State private var health: String
    @State private var tags: String
    @State private var capabilities: String

    init(editor: ResourceEditor, onSave: @escaping ([String: DynamicJSON]) async -> Void) {
        self.editor = editor
        self.onSave = onSave
        _resourceID = State(initialValue: editor.existingID)
        _label = State(initialValue: editor.initialLabel)
        _resourceType = State(initialValue: editor.initialType)
        _platform = State(initialValue: editor.initialPlatform)
        _health = State(initialValue: editor.initialHealth)
        _tags = State(initialValue: editor.initialTags)
        _capabilities = State(initialValue: editor.initialCapabilities)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("标识") {
                    TextField("Resource ID", text: $resourceID)
                        .textInputAutocapitalization(.never)
                        .disabled(!editor.isNew)
                    TextField("名称", text: $label)
                    TextField("类型", text: $resourceType)
                        .textInputAutocapitalization(.never)
                    TextField("平台", text: $platform)
                        .textInputAutocapitalization(.never)
                }
                Section("治理") {
                    Picker("健康状态", selection: $health) {
                        Text("Ready").tag("ready")
                        Text("Degraded").tag("degraded")
                        Text("Unknown").tag("unknown")
                        Text("Offline").tag("offline")
                    }
                    TextField("标签，逗号分隔", text: $tags)
                    TextField("能力，逗号分隔", text: $capabilities, axis: .vertical)
                        .lineLimit(2...5)
                }
            }
            .navigationTitle(editor.isNew ? "新增 Resource" : "编辑 Resource")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") { Task { await save() } }
                        .disabled(resourceID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
    }

    private func save() async {
        let resource: [String: DynamicJSON] = [
            "resource_id": .string(resourceID.trimmingCharacters(in: .whitespacesAndNewlines)),
            "label": .string(label.trimmingCharacters(in: .whitespacesAndNewlines)),
            "resource_type": .string(resourceType.trimmingCharacters(in: .whitespacesAndNewlines)),
            "platform": .string(platform.trimmingCharacters(in: .whitespacesAndNewlines)),
            "health_status": .string(health),
            "tags": .array(csv(tags).map(DynamicJSON.string)),
            "supported_capabilities": .array(csv(capabilities).map(DynamicJSON.string))
        ]
        await onSave(["action": .string(editor.isNew ? "create" : "update"), "resource": .object(resource)])
        dismiss()
    }

    private func csv(_ value: String) -> [String] {
        value.split(separator: ",").map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
    }
}
