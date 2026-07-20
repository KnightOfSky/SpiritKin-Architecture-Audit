import SwiftUI

struct WorkflowsView: View {
    @EnvironmentObject private var store: TerminalStore

    var body: some View {
        NavigationStack {
            List {
                Section("能力板块") {
                    ForEach(WorkflowDomain.allCases) { domain in
                        NavigationLink {
                            DomainWorkflowsView(domain: domain)
                        } label: {
                            HStack(spacing: 12) {
                                Image(systemName: domain.systemImage)
                                    .foregroundStyle(domain == .ecommerce ? FantasyTheme.secondary : FantasyTheme.primary)
                                    .frame(width: 36, height: 36)
                                    .background(FantasyTheme.surface2, in: RoundedRectangle(cornerRadius: 8))
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(domain.title)
                                    Text(domain.subtitle)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(2)
                                }
                                Spacer()
                                Text("\(definitions(in: domain).count)")
                                    .font(.caption.weight(.semibold))
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }

                Section("运行中心") {
                    NavigationLink {
                        DomainManagementView()
                    } label: {
                        Label("领域管理", systemImage: "folder.badge.gearshape")
                    }
                    NavigationLink {
                        CapabilityPoolsView()
                    } label: {
                        Label("Skill / Workflow 池", systemImage: "shippingbox")
                    }
                    NavigationLink {
                        CapabilityModulesView()
                    } label: {
                        Label("能力模块开关", systemImage: "switch.2")
                    }
                    NavigationLink {
                        ResourceManagementView()
                    } label: {
                        Label("Resource 管理", systemImage: "externaldrive.connected.to.line.below")
                    }
                    NavigationLink {
                        RuntimeMonitorView()
                    } label: {
                        Label("Workspace / Worker 监控", systemImage: "waveform.path.ecg")
                    }
                    NavigationLink {
                        WorkflowCompositionView()
                    } label: {
                        Label("组合工作流", systemImage: "point.3.connected.trianglepath.dotted")
                    }
                    ForEach(store.workflowRuns.prefix(6)) { run in
                        NavigationLink {
                            WorkflowRunDetailView(run: run)
                        } label: {
                            RowLine(
                                title: run.workflowName,
                                subtitle: "\(run.runId) · \(run.updatedAt)",
                                status: run.status
                            )
                        }
                    }
                }
            }
            .fantasyCanvas()
            .navigationTitle("板块")
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

    private func definitions(in domain: WorkflowDomain) -> [WorkflowDefinitionItem] {
        store.workflowDefinitions.filter { $0.domain == domain }
    }
}

private struct DomainWorkflowsView: View {
    @EnvironmentObject private var store: TerminalStore
    let domain: WorkflowDomain
    @State private var inputsJSON = #"{"project_root":"D:/SpiritKinAI"}"#
    @State private var selectedWorkflow = ""

    private var definitions: [WorkflowDefinitionItem] {
        store.workflowDefinitions.filter { $0.domain == domain }
    }

    private var runs: [WorkflowRunItem] {
        let names = Set(definitions.map(\.name))
        return store.workflowRuns.filter { names.contains($0.workflowName) }
    }

    var body: some View {
        Form {
            Section {
                Label(domain.subtitle, systemImage: domain.systemImage)
                    .foregroundStyle(.secondary)
            }

            if domain == .ecommerce {
                Section("电商主控") {
                    NavigationLink {
                        EcommerceTerminalView()
                    } label: {
                        Label("打开电商运营 Terminal", systemImage: "terminal")
                    }
                    Text("商品素材、工作区、Android 上架、配对与审计均在电商领域内管理。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Section("启动工作流") {
                if definitions.isEmpty {
                    Text("当前领域暂无已注册工作流。")
                        .foregroundStyle(.secondary)
                } else {
                    Picker("工作流", selection: $selectedWorkflow) {
                        ForEach(definitions) { item in
                            Text(item.displayName).tag(item.name)
                        }
                    }
                    TextEditor(text: $inputsJSON)
                        .frame(minHeight: 96)
                        .font(.system(.footnote, design: .monospaced))
                    Button("启动运行") {
                        Task { await startRun() }
                    }
                    .disabled(selectedWorkflow.isEmpty)
                }
            }

            Section("最近运行") {
                if runs.isEmpty {
                    Text("暂无运行记录")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(runs) { run in
                        NavigationLink {
                            WorkflowRunDetailView(run: run)
                        } label: {
                            RowLine(title: run.workflowName, subtitle: run.updatedAt, status: run.status)
                        }
                    }
                }
            }
        }
        .fantasyCanvas()
        .navigationTitle(domain.title)
        .onAppear {
            if selectedWorkflow.isEmpty {
                selectedWorkflow = definitions.first?.name ?? ""
            }
        }
    }

    private func startRun() async {
        do {
            let inputs = try JSONHelpers.parseObject(inputsJSON)
            await store.sendAction([
                "action": .string("start_run"),
                "workflow_name": .string(selectedWorkflow),
                "inputs": .object(inputs)
            ], successMessage: "Workflow started")
        } catch {
            store.statusMessage = error.localizedDescription
        }
    }
}

private struct DomainEditor: Identifiable {
    let id = UUID()
    let existingID: String
    let initialTitle: String
    let initialDescription: String
    let isBuiltIn: Bool
}

private struct DomainManagementView: View {
    @EnvironmentObject private var store: TerminalStore
    @State private var editor: DomainEditor?

    private var domains: [DynamicJSON] { store.domainSnapshot["domains"].arrayValue }

    var body: some View {
        List {
            if domains.isEmpty {
                Text("暂无领域")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(domains.indices, id: \.self) { index in
                    domainRow(domains[index])
                }
            }
        }
        .fantasyCanvas()
        .navigationTitle("领域管理")
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button {
                    editor = DomainEditor(existingID: "", initialTitle: "", initialDescription: "", isBuiltIn: false)
                } label: {
                    Image(systemName: "plus")
                }
                .accessibilityLabel("新增领域")
                Button { Task { await store.refreshDomains(force: true) } } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .accessibilityLabel("刷新领域")
            }
        }
        .sheet(item: $editor) { item in
            DomainEditorSheet(editor: item) { payload in
                await store.domainAction(payload, successMessage: "领域已更新")
            }
        }
        .task { await store.refreshDomains() }
    }

    @ViewBuilder
    private func domainRow(_ item: DynamicJSON) -> some View {
        let domainID = item["id"].stringValue
        let editable = item["editable"].boolValue
        let row = RowLine(
            title: item["title"].stringValue,
            subtitle: item["description"].stringValue,
            status: item["enabled"].boolValue ? "启用" : "停用"
        )
        if editable {
            Button {
                editor = DomainEditor(existingID: domainID, initialTitle: item["title"].stringValue, initialDescription: item["description"].stringValue, isBuiltIn: item["built_in"].boolValue)
            } label: { row }
            .buttonStyle(.plain)
            .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                if item["deletable"].boolValue {
                    Button(role: .destructive) {
                        Task {
                            await store.domainAction(["action": .string("delete"), "id": .string(domainID)], successMessage: "领域已删除")
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

private struct DomainEditorSheet: View {
    let editor: DomainEditor
    let onSave: ([String: DynamicJSON]) async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var domainID: String
    @State private var title: String
    @State private var description: String

    init(editor: DomainEditor, onSave: @escaping ([String: DynamicJSON]) async -> Void) {
        self.editor = editor
        self.onSave = onSave
        _domainID = State(initialValue: editor.existingID)
        _title = State(initialValue: editor.initialTitle)
        _description = State(initialValue: editor.initialDescription)
    }

    var body: some View {
        NavigationStack {
            Form {
                TextField("领域 ID", text: $domainID)
                    .textInputAutocapitalization(.never)
                    .disabled(editor.isBuiltIn)
                TextField("名称", text: $title)
                TextField("描述", text: $description, axis: .vertical)
                    .lineLimit(3...6)
            }
            .navigationTitle(editor.existingID.isEmpty ? "新增领域" : "编辑领域")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") { Task { await save() } }
                        .disabled(domainID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
    }

    private func save() async {
        let cleanID = domainID.trimmingCharacters(in: .whitespacesAndNewlines)
        let action = editor.existingID.isEmpty ? "create" : "update"
        await onSave([
            "action": .string(action),
            "id": .string(cleanID),
            "title": .string(title.trimmingCharacters(in: .whitespacesAndNewlines)),
            "description": .string(description.trimmingCharacters(in: .whitespacesAndNewlines))
        ])
        dismiss()
    }
}

private struct WorkflowCompositionView: View {
    @EnvironmentObject private var store: TerminalStore
    @State private var name = "custom.workflow.ios_native_combo.v1"
    @State private var displayName = "iOS Native 组合工作流"
    @State private var mode = "serial"
    @State private var components = "ecommerce.auto_listing.v1\ncontent.video_generation.v1"

    var body: some View {
        Form {
            Section("组合定义") {
                TextField("Workflow ID", text: $name)
                TextField("显示名称", text: $displayName)
                Picker("模式", selection: $mode) {
                    Text("串行").tag("serial")
                    Text("并行").tag("parallel")
                }
                TextEditor(text: $components)
                    .frame(minHeight: 120)
                    .font(.system(.footnote, design: .monospaced))
                Button("保存组合") { Task { await compose(startAfterSave: false) } }
                Button("保存并启动") { Task { await compose(startAfterSave: true) } }
            }
        }
        .fantasyCanvas()
        .navigationTitle("组合工作流")
    }

    private func compose(startAfterSave: Bool) async {
        let items = components
            .split(whereSeparator: { $0 == "\n" || $0 == "," })
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .map { DynamicJSON.object(["workflow_name": .string($0)]) }
        await store.sendAction([
            "action": .string("compose_definition"),
            "workflow_name": .string(name),
            "display_name": .string(displayName),
            "mode": .string(mode),
            "components": .array(items)
        ], successMessage: "Composition saved")
        if startAfterSave {
            await store.sendAction([
                "action": .string("start_run"),
                "workflow_name": .string(name),
                "inputs": .object([:])
            ], successMessage: "Workflow started")
        }
    }
}
