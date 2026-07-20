import Foundation
import SwiftUI

private struct PoolEditor: Identifiable {
    let id = UUID()
    let pool: String
    let existingName: String
    let initialDescription: String
    let initialDomain: String

    var isNew: Bool { existingName.isEmpty }
}

private struct GrowthCandidateEditor: Identifiable {
    let id: String
    let candidate: DynamicJSON
    let escalationTargets: [String]
}

struct CapabilityPoolsView: View {
    @EnvironmentObject private var store: TerminalStore
    @State private var editor: PoolEditor?
    @State private var growthEditor: GrowthCandidateEditor?
    @State private var probingSandbox = false
    @State private var sandboxProbeConfirmationPresented = false

    var body: some View {
        List {
            Section("Skill 池") {
                let skills = store.poolSnapshot.skills["items"].arrayValue
                if skills.isEmpty {
                    Text("暂无 Skill")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(skills.indices, id: \.self) { index in
                        skillRow(skills[index])
                    }
                }
            }
            Section("Workflow 池") {
                let workflows = store.poolSnapshot.workflows["items"].arrayValue
                if workflows.isEmpty {
                    Text("暂无 Workflow")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(workflows.indices, id: \.self) { index in
                        workflowRow(workflows[index])
                    }
                }
            }
            Section("Growth Runtime") {
                let counts = store.growthSnapshot["status_counts"]
                let candidateCount = store.growthSnapshot["candidate_count"].intValue
                let pending = counts["candidate"].intValue
                let registered = counts["registered"].intValue
                let artifactCount = store.growthSnapshot["builder_artifacts"]["count"].intValue
                let sandboxStatus = store.growthSnapshot["sandbox_runtime"]["status"].stringValue
                Label(
                    "候选 \(candidateCount) · 待审核 \(pending) · 已注册 \(registered) · 工件 \(artifactCount)",
                    systemImage: pending > 0 ? "exclamationmark.triangle" : "checkmark.circle"
                )
                Text("能力缺口、Workflow/Skill/Tool 成长统一由桌面端治理；候选不会自动激活。")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                LabeledContent("沙箱运行时", value: sandboxStatus.isEmpty ? "not_probed" : sandboxStatus)
                    .font(.footnote)
                if !store.growthSnapshot["sandbox_runtime"]["candidate_execution_enabled"].boolValue {
                    Button(probingSandbox ? "检查中…" : "检查沙箱运行时", systemImage: "shippingbox") {
                        sandboxProbeConfirmationPresented = true
                    }
                    .disabled(probingSandbox)
                }
                let candidates = store.growthSnapshot["candidates"].arrayValue.sorted { left, right in
                    let leftIsCurrent = left["workspace_id"].stringValue == store.workspaceID
                    let rightIsCurrent = right["workspace_id"].stringValue == store.workspaceID
                    return leftIsCurrent && !rightIsCurrent
                }
                if candidates.isEmpty {
                    Text("当前没有待处理候选")
                        .foregroundStyle(.secondary)
                } else {
                    let visibleCandidates = Array(candidates.prefix(8))
                    ForEach(visibleCandidates.indices, id: \.self) { index in
                        let candidate = visibleCandidates[index]
                        growthCandidateRow(candidate)
                    }
                }
            }
        }
        .fantasyCanvas()
        .navigationTitle("Skill / Workflow 池")
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                Menu {
                    Button("新增 Skill", systemImage: "sparkles") {
                        editor = PoolEditor(pool: "skills", existingName: "", initialDescription: "", initialDomain: "general")
                    }
                    Button("新增 Workflow", systemImage: "point.3.connected.trianglepath.dotted") {
                        editor = PoolEditor(pool: "workflows", existingName: "", initialDescription: "", initialDomain: "general")
                    }
                } label: {
                    Image(systemName: "plus")
                }
                .accessibilityLabel("新增 Skill 或 Workflow")
                Button { Task { await store.refreshPools(force: true); await store.refreshGrowth(force: true) } } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .accessibilityLabel("刷新能力池和能力成长")
            }
        }
        .sheet(item: $editor) { item in
            PoolEditorSheet(editor: item) { payload in
                await store.poolAction(payload, successMessage: "能力池已更新")
            }
        }
        .sheet(item: $growthEditor) { item in
            GrowthCandidateSheet(
                candidate: item.candidate,
                escalationTargets: item.escalationTargets,
                sandboxRuntime: store.growthSnapshot["sandbox_runtime"]
            ) { payload in
                let action = payload["action"]?.stringValue ?? ""
                let message = action == "prepare_builder_artifact"
                    ? "Builder 工件已准备"
                    : (action == "research_candidate" ? "公开仓库研究已完成" : (action == "prepare_sandbox_bundle" ? "Sandbox Bundle 已准备" : (action == "verify_builder_artifact" ? "Builder 沙箱预检已完成" : (action == "execute_builder_sandbox" ? "隔离候选测试已完成" : (action == "escalate_candidate" ? "Growth 下一级候选已创建" : "Growth 治理状态已更新")))))
                await store.growthAction(payload, successMessage: message)
                await store.refreshGrowth(force: true)
            }
        }
        .confirmationDialog(
            "探测 Growth 沙箱运行时？",
            isPresented: $sandboxProbeConfirmationPresented,
            titleVisibility: .visible
        ) {
            Button("确认探测") {
                probingSandbox = true
                Task {
                    await store.growthAction(
                        [
                            "action": .string("probe_sandbox_runtime"),
                            "workspace_id": .string(store.workspaceID),
                            "confirmed": .bool(true)
                        ],
                        successMessage: "沙箱运行时已探测"
                    )
                    probingSandbox = false
                }
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("只启动固定的受信探针容器，不运行候选代码、不挂载 workspace、不联网，也不会推进阶段或激活能力。")
        }
        .task { await store.refreshPools(); await store.refreshGrowth() }
    }

    @ViewBuilder
    private func growthCandidateRow(_ candidate: DynamicJSON) -> some View {
        let candidateID = candidate["candidate_id"].stringValue
        let candidateWorkspace = candidate["workspace_id"].stringValue
        let canManage = !candidateID.isEmpty && !store.workspaceID.isEmpty && candidateWorkspace == store.workspaceID
        let builderArtifact = candidate["evidence"]["builder_artifact"]
        VStack(alignment: .leading, spacing: 6) {
            RowLine(
                title: "\(candidate[\"kind\"].stringValue) · \(candidate[\"title\"].stringValue)",
                subtitle: "状态 \(candidate[\"status\"].stringValue) · 阶段 \(candidate[\"current_stage\"].stringValue) · \(candidateWorkspace.isEmpty ? \"全局\" : candidateWorkspace)",
                status: candidate["promotion_status"].stringValue
            )
            if !builderArtifact["artifact_id"].stringValue.isEmpty {
                Text("Builder 已准备 · 匹配 \(builderArtifact["inventory_match_count"].intValue) · \(builderArtifact["registry_target"].stringValue)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if canManage {
                Button {
                    let kind = candidate["kind"].stringValue
                    let targets = store.growthSnapshot["pipeline"]["escalation_targets"][kind].arrayValue.map(\.stringValue)
                    growthEditor = GrowthCandidateEditor(id: candidateID, candidate: candidate, escalationTargets: targets)
                } label: {
                    Label("管理治理状态", systemImage: "checkmark.shield")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            } else if candidateWorkspace.isEmpty {
                Text("全局候选仅允许桌面治理")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private func skillRow(_ item: DynamicJSON) -> some View {
        let name = item["name"].stringValue
        let editable = item["editable"].boolValue
        let row = RowLine(
            title: item["name"].stringValue,
            subtitle: "\(item["domain"].stringValue) · \(item["description"].stringValue)",
            status: item["status"].stringValue
        )
        if editable {
            Button {
                editor = PoolEditor(pool: "skills", existingName: name, initialDescription: item["description"].stringValue, initialDomain: item["domain"].stringValue)
            } label: {
                row
            }
            .buttonStyle(.plain)
            .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                Button(role: .destructive) {
                    Task { await delete(pool: "skills", name: name) }
                } label: {
                    Label("删除", systemImage: "trash")
                }
            }
        } else {
            row
        }
    }

    @ViewBuilder
    private func workflowRow(_ item: DynamicJSON) -> some View {
        let name = item["name"].stringValue
        let editable = item["editable"].boolValue
        let row = RowLine(
            title: item["display_name"].stringValue,
            subtitle: "\(item["domain"].stringValue) · \(item["name"].stringValue)",
            status: "\(item["node_count"].intValue) 节点"
        )
        if editable {
            Button {
                editor = PoolEditor(pool: "workflows", existingName: name, initialDescription: item["description"].stringValue, initialDomain: item["domain"].stringValue)
            } label: {
                row
            }
            .buttonStyle(.plain)
            .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                Button(role: .destructive) {
                    Task { await delete(pool: "workflows", name: name) }
                } label: {
                    Label("删除", systemImage: "trash")
                }
            }
        } else {
            row
        }
    }

    private func delete(pool: String, name: String) async {
        var payload: [String: DynamicJSON] = [
            "pool": .string(pool),
            "action": .string("delete"),
            "name": .string(name)
        ]
        if pool == "workflows" {
            payload["workflow_name"] = .string(name)
        }
        await store.poolAction(payload, successMessage: "已删除 \(name)")
    }
}

private struct GrowthCandidateSheet: View {
    let candidate: DynamicJSON
    let escalationTargets: [String]
    let sandboxRuntime: DynamicJSON
    let onSubmit: ([String: DynamicJSON]) async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var evidence = ""
    @State private var researchKeywords = ""
    @State private var sandboxBundleJSON = #"{"files":[{"path":"probe.py","content":"print('sandbox')\n"}],"command":["python","-I","probe.py"]}"#
    @State private var sandboxBundleError = ""
    @State private var benchmarkVersion = ""
    @State private var benchmarkBaselineVersion = ""
    @State private var benchmarkDataset = ""
    @State private var benchmarkSource = ""
    @State private var benchmarkBeforeJSON = ""
    @State private var benchmarkAfterJSON = ""
    @State private var benchmarkError = ""
    @State private var verifyConfirmationPresented = false
    @State private var researchConfirmationPresented = false
    @State private var executionConfirmationPresented = false
    @State private var benchmarkConfirmationPresented = false
    @State private var modelJuryConfirmationPresented = false
    @State private var escalationTarget: String

    init(
        candidate: DynamicJSON,
        escalationTargets: [String],
        sandboxRuntime: DynamicJSON,
        onSubmit: @escaping ([String: DynamicJSON]) async -> Void
    ) {
        self.candidate = candidate
        self.escalationTargets = escalationTargets
        self.sandboxRuntime = sandboxRuntime
        self.onSubmit = onSubmit
        _escalationTarget = State(initialValue: escalationTargets.first ?? "human")
    }

    private var stages: [String] { candidate["stages"].arrayValue.map(\.stringValue) }
    private var currentStage: String { candidate["current_stage"].stringValue }
    private var nextStage: String {
        guard let index = stages.firstIndex(of: currentStage), index + 1 < stages.count else { return "" }
        return stages[index + 1]
    }
    private var hasEvidence: Bool {
        evidence.trimmingCharacters(in: .whitespacesAndNewlines).count >= 2
    }
    private var builderArtifact: DynamicJSON { candidate["evidence"]["builder_artifact"] }
    private var remoteResearch: DynamicJSON { candidate["evidence"]["remote_research"] }
    private var sandboxBundle: DynamicJSON { candidate["evidence"]["sandbox_bundle"] }
    private var sandboxExecution: DynamicJSON { candidate["evidence"]["sandbox_execution"] }
    private var benchmarkReport: DynamicJSON { candidate["evidence"]["benchmark_report"] }
    private var builderPrepared: Bool { !builderArtifact["artifact_id"].stringValue.isEmpty }
    private var canPrepareBuilder: Bool {
        !["rejected", "registered", "escalated", "needs_human"].contains(candidate["status"].stringValue)
    }
    private var canVerifyBuilder: Bool {
        guard builderPrepared, candidate["status"].stringValue == "candidate" else { return false }
        let kind = candidate["kind"].stringValue
        let allowed: [String: Set<String>] = [
            "capability": ["design"],
            "workflow": ["dry_run", "benchmark"],
            "skill": ["sandbox", "dry_run", "benchmark"],
            "tool": ["sandbox", "dry_run", "benchmark"],
            "code": ["sandbox", "dry_run", "benchmark"],
            "model": ["benchmark"]
        ]
        return allowed[kind, default: []].contains(currentStage)
    }
    private var canResearch: Bool {
        candidate["status"].stringValue == "candidate" && !["review", "registry"].contains(currentStage)
    }
    private var canPrepareSandboxBundle: Bool {
        guard builderPrepared, candidate["status"].stringValue == "candidate" else { return false }
        let allowed: [String: Set<String>] = [
            "skill": ["design", "sandbox", "dry_run", "benchmark"],
            "tool": ["research", "sandbox", "dry_run", "benchmark"],
            "code": ["design", "sandbox", "dry_run", "benchmark"]
        ]
        return allowed[candidate["kind"].stringValue, default: []].contains(currentStage)
    }
    private var canExecuteSandbox: Bool {
        candidate["status"].stringValue == "candidate"
            && ["skill", "tool", "code"].contains(candidate["kind"].stringValue)
            && ["sandbox", "dry_run", "benchmark"].contains(currentStage)
            && !sandboxBundle["bundle_id"].stringValue.isEmpty
            && builderArtifact["verification_status"].stringValue == "passed"
            && sandboxRuntime["candidate_execution_enabled"].boolValue
    }
    private var canRecordBenchmark: Bool {
        candidate["status"].stringValue == "candidate" && currentStage == "benchmark"
    }
    private var canAdvanceStage: Bool {
        !nextStage.isEmpty
            && currentStage != "review"
            && (nextStage != "review" || benchmarkReport["promotion_status"].stringValue == "passed")
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("候选") {
                    Text(candidate["title"].stringValue)
                        .font(.headline)
                    Text("\(candidate[\"kind\"].stringValue) · \(currentStage) · 风险 \(candidate[\"risk\"][\"level\"].stringValue)")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    let lineage = candidate["lineage"]
                    let parentID = lineage["parent_candidate_id"].stringValue
                    LabeledContent("谱系", value: parentID.isEmpty ? "根候选" : "第 \(lineage[\"depth\"].intValue) 层")
                    if !parentID.isEmpty {
                        Text("父候选 \(parentID)")
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                    let resolution = candidate["resolution"]
                    if resolution["status"].stringValue != "unrouted" {
                        LabeledContent("路由", value: "\(resolution[\"status\"].stringValue) · \(resolution[\"target_kind\"].stringValue)")
                    }
                }
                Section("证据 / 审核理由") {
                    TextEditor(text: $evidence)
                        .frame(minHeight: 120)
                    Text("每次提交都必须说明来源、测试或评测结果；登记不会自动激活能力。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                Section("Builder 工件") {
                    TextField("公开搜索关键词", text: $researchKeywords)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Text("留空时使用候选需求；只填写可公开发送到 GitHub 的词语，不要包含凭据或隐私信息。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    if !remoteResearch["report_id"].stringValue.isEmpty {
                        LabeledContent("公开研究", value: "\(remoteResearch[\"result_count\"].intValue) 条")
                        LabeledContent("报告", value: remoteResearch["report_id"].stringValue)
                        let repositories = Array(remoteResearch["repositories"].arrayValue.prefix(2))
                        ForEach(repositories.indices, id: \.self) { index in
                            let repository = repositories[index]
                            if let url = URL(string: repository["url"].stringValue) {
                                Link(destination: url) {
                                    Label(
                                        "\(repository[\"full_name\"].stringValue) · \(repository[\"license_spdx\"].stringValue)",
                                        systemImage: repository["needs_license_review"].boolValue ? "exclamationmark.shield" : "checkmark.shield"
                                    )
                                }
                            }
                        }
                    } else {
                        Text("公开仓库研究尚未运行")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    if canResearch {
                        Button("研究公开仓库元数据", systemImage: "magnifyingglass") {
                            researchConfirmationPresented = true
                        }
                    }
                    if builderPrepared {
                        LabeledContent("状态", value: builderArtifact["status"].stringValue)
                        LabeledContent("本地匹配", value: "\(builderArtifact["inventory_match_count"].intValue)")
                        LabeledContent("验证", value: builderArtifact["verification_status"].stringValue)
                        LabeledContent("Registry", value: builderArtifact["registry_target"].stringValue)
                        if builderArtifact["human_required"].boolValue {
                            Label("需要人工补充来源", systemImage: "person.crop.circle.badge.exclamationmark")
                                .foregroundStyle(.orange)
                        }
                    }
                    if canPrepareBuilder {
                        Button(builderPrepared ? "重新准备 Builder 工件" : "准备 Builder 工件", systemImage: "shippingbox") {
                            Task { await prepareBuilder() }
                        }
                    }
                    if canVerifyBuilder {
                        Button("运行静态沙箱预检", systemImage: "checkmark.shield") {
                            verifyConfirmationPresented = true
                        }
                    }
                    if canPrepareSandboxBundle {
                        DisclosureGroup("Sandbox Bundle") {
                            TextEditor(text: $sandboxBundleJSON)
                                .font(.caption.monospaced())
                                .frame(minHeight: 120)
                            if !sandboxBundleError.isEmpty {
                                Label(sandboxBundleError, systemImage: "exclamationmark.triangle")
                                    .font(.footnote)
                                    .foregroundStyle(.red)
                            }
                            Button("准备代码 Bundle", systemImage: "shippingbox.and.arrow.backward") {
                                Task { await prepareSandboxBundle() }
                            }
                        }
                    }
                    LabeledContent(
                        "隔离执行器",
                        value: sandboxRuntime["candidate_execution_enabled"].boolValue ? "可用" : "关闭"
                    )
                    if !sandboxBundle["bundle_id"].stringValue.isEmpty {
                        LabeledContent("Bundle", value: sandboxBundle["bundle_id"].stringValue)
                        LabeledContent("Bundle 文件", value: "\(sandboxBundle["file_count"].intValue)")
                    }
                    if !sandboxExecution["execution_id"].stringValue.isEmpty {
                        LabeledContent(
                            "隔离测试",
                            value: "\(sandboxExecution["status"].stringValue) · exit \(sandboxExecution["exit_code"].intValue)"
                        )
                    }
                    if canExecuteSandbox {
                        Button("运行隔离候选测试", systemImage: "cube.transparent") {
                            executionConfirmationPresented = true
                        }
                    }
                }
                if canRecordBenchmark || !benchmarkReport["benchmark_id"].stringValue.isEmpty {
                    Section("Benchmark") {
                        if !benchmarkReport["benchmark_id"].stringValue.isEmpty {
                            LabeledContent("Promotion Gate", value: benchmarkReport["promotion_status"].stringValue)
                            LabeledContent("总分", value: String(format: "%.1f", benchmarkReport["overall_score"].doubleValue))
                            LabeledContent("Before / After", value: String(format: "%+.1f", benchmarkReport["overall_delta"].doubleValue))
                            LabeledContent("成功率", value: String(format: "%.1f%%", benchmarkReport["success_rate"].doubleValue * 100))
                            LabeledContent("质量", value: String(format: "%.1f", benchmarkReport["quality_score"].doubleValue))
                        }
                        if canRecordBenchmark {
                            DisclosureGroup("记录 Before / After") {
                                TextField("版本", text: $benchmarkVersion)
                                    .textInputAutocapitalization(.never)
                                TextField("基线版本", text: $benchmarkBaselineVersion)
                                    .textInputAutocapitalization(.never)
                                TextField("数据集", text: $benchmarkDataset)
                                    .textInputAutocapitalization(.never)
                                if !["workflow", "skill", "tool", "code"].contains(candidate["kind"].stringValue) {
                                    TextField("测量来源", text: $benchmarkSource)
                                        .textInputAutocapitalization(.never)
                                }
                                Text("Before JSON")
                                    .font(.footnote)
                                    .foregroundStyle(.secondary)
                                Text("字段：success_rate、latency_ms、cost、retry_count、review_count、quality_score")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                TextEditor(text: $benchmarkBeforeJSON)
                                    .font(.caption.monospaced())
                                    .frame(minHeight: 96)
                                Text("After JSON")
                                    .font(.footnote)
                                    .foregroundStyle(.secondary)
                                TextEditor(text: $benchmarkAfterJSON)
                                    .font(.caption.monospaced())
                                    .frame(minHeight: 96)
                                if candidate["kind"].stringValue == "model" {
                                    Label("等待服务端 Model Jury", systemImage: "person.3.sequence")
                                    Text("客户端不能手填评审结论；需要可审计的多模型评审报告。")
                                        .font(.footnote)
                                        .foregroundStyle(.secondary)
                                    if !benchmarkReport["benchmark_id"].stringValue.isEmpty {
                                        Button("请求 Model Jury", systemImage: "person.3.sequence") {
                                            modelJuryConfirmationPresented = true
                                        }
                                    }
                                }
                                if !benchmarkError.isEmpty {
                                    Label(benchmarkError, systemImage: "exclamationmark.triangle")
                                        .font(.footnote)
                                        .foregroundStyle(.red)
                                }
                                Button("记录结构化 Benchmark", systemImage: "gauge.with.dots.needle.67percent") {
                                    validateBenchmark()
                                }
                            }
                        }
                    }
                }
                Section("治理动作") {
                    if candidate["status"].stringValue == "candidate", !escalationTargets.isEmpty {
                        Picker("下一类 Builder", selection: $escalationTarget) {
                            ForEach(escalationTargets, id: \.self) { target in
                                Text(growthKindLabel(target)).tag(target)
                            }
                        }
                        Button("创建下一级候选", systemImage: "arrow.triangle.branch") {
                            Task { await submitEscalation() }
                        }
                        .disabled(!hasEvidence)
                    }
                    if candidate["status"].stringValue == "candidate", canAdvanceStage {
                        Button("提交下一阶段：\(nextStage)", systemImage: "arrow.right") {
                            Task { await submit(action: "advance_stage") }
                        }
                        .disabled(!hasEvidence)
                    }
                    if candidate["status"].stringValue == "candidate", currentStage == "review" {
                        Button("审核通过", systemImage: "checkmark.circle") {
                            Task { await submit(action: "review_candidate", decision: "approve") }
                        }
                        .disabled(!hasEvidence)
                        Button(role: .destructive) {
                            Task { await submit(action: "review_candidate", decision: "reject") }
                        } label: {
                            Label("驳回候选", systemImage: "xmark.circle")
                        }
                        .disabled(!hasEvidence)
                    }
                    if candidate["status"].stringValue == "approved", currentStage == "review" {
                        Button("登记 Registry", systemImage: "archivebox") {
                            Task { await submit(action: "register_candidate") }
                        }
                        .disabled(!hasEvidence)
                    }
                    if candidate["status"].stringValue == "registered" {
                        Label("已登记，仍需独立激活", systemImage: "lock")
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("Growth 治理")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
            }
            .confirmationDialog(
                "运行 Builder 静态沙箱预检？",
                isPresented: $verifyConfirmationPresented,
                titleVisibility: .visible
            ) {
                Button("确认运行") { Task { await verifyBuilder() } }
                Button("取消", role: .cancel) {}
            } message: {
                Text("只校验工件完整性、workspace、写入范围和本地 Registry 匹配；不会联网、安装、执行外部代码、推进阶段或激活能力。")
            }
            .confirmationDialog(
                "研究公开 GitHub 仓库？",
                isPresented: $researchConfirmationPresented,
                titleVisibility: .visible
            ) {
                Button("确认研究") { Task { await researchCandidate() } }
                Button("取消", role: .cancel) {}
            } message: {
                Text("只读取最多 5 条公开仓库元数据并生成受管报告；不会克隆、下载、安装、执行、推进阶段或激活能力。")
            }
            .confirmationDialog(
                "运行隔离候选代码？",
                isPresented: $executionConfirmationPresented,
                titleVisibility: .visible
            ) {
                Button("确认运行") { Task { await executeSandbox() } }
                Button("取消", role: .cancel) {}
            } message: {
                Text("候选代码只在无网络、无主机挂载、只读、非 root 且受 CPU/内存/PID 限制的容器中运行；结果不会推进阶段或激活能力。")
            }
            .confirmationDialog(
                "记录 Before / After Benchmark？",
                isPresented: $benchmarkConfirmationPresented,
                titleVisibility: .visible
            ) {
                Button("确认记录") { Task { await recordBenchmark() } }
                Button("取消", role: .cancel) {}
            } message: {
                Text("服务端将计算总分与 Promotion Gate；报告不会推进阶段、登记或激活能力。")
            }
            .confirmationDialog(
                "请求 Model Jury？",
                isPresented: $modelJuryConfirmationPresented,
                titleVisibility: .visible
            ) {
                Button("确认请求") { Task { await runModelJury() } }
                Button("取消", role: .cancel) {}
            } message: {
                Text("受管 Benchmark 摘要将发送到已配置的 Review Committee；至少两个独立结构化评审通过后才开放 Review。")
            }
        }
    }

    private func submit(action: String, decision: String = "") async {
        let summary = evidence.trimmingCharacters(in: .whitespacesAndNewlines)
        guard summary.count >= 2 else { return }
        var payload: [String: DynamicJSON] = [
            "action": .string(action),
            "candidate_id": .string(candidate["candidate_id"].stringValue),
            "workspace_id": .string(candidate["workspace_id"].stringValue),
            "confirmed": .bool(true),
            "evidence": .object([
                "summary": .string(summary),
                "source": .string("ios_native_controller")
            ])
        ]
        if action == "advance_stage" { payload["stage"] = .string(nextStage) }
        if action == "review_candidate" {
            payload["decision"] = .string(decision)
            payload["reason"] = .string(summary)
        }
        if action == "register_candidate" {
            payload["registry_evidence"] = payload["evidence"]
        }
        await onSubmit(payload)
        dismiss()
    }

    private func submitEscalation() async {
        let summary = evidence.trimmingCharacters(in: .whitespacesAndNewlines)
        guard summary.count >= 2, escalationTargets.contains(escalationTarget) else { return }
        await onSubmit([
            "action": .string("escalate_candidate"),
            "candidate_id": .string(candidate["candidate_id"].stringValue),
            "workspace_id": .string(candidate["workspace_id"].stringValue),
            "target_kind": .string(escalationTarget),
            "reason": .string(summary),
            "confirmed": .bool(true),
            "evidence": .object([
                "summary": .string(summary),
                "source": .string("ios_native_controller")
            ])
        ])
        dismiss()
    }

    private func growthKindLabel(_ kind: String) -> String {
        [
            "workflow": "Workflow Builder",
            "skill": "Skill Builder",
            "tool": "Tool Builder",
            "code": "Code Builder",
            "model": "Model Builder",
            "human": "转人工处理"
        ][kind] ?? kind
    }

    private func prepareBuilder() async {
        let payload: [String: DynamicJSON] = [
            "action": .string("prepare_builder_artifact"),
            "candidate_id": .string(candidate["candidate_id"].stringValue),
            "workspace_id": .string(candidate["workspace_id"].stringValue)
        ]
        await onSubmit(payload)
        dismiss()
    }

    private func researchCandidate() async {
        var payload: [String: DynamicJSON] = [
            "action": .string("research_candidate"),
            "candidate_id": .string(candidate["candidate_id"].stringValue),
            "workspace_id": .string(candidate["workspace_id"].stringValue),
            "confirmed": .bool(true)
        ]
        let keywords = researchKeywords.trimmingCharacters(in: .whitespacesAndNewlines)
        if !keywords.isEmpty {
            payload["keywords"] = .string(keywords)
        }
        await onSubmit(payload)
        dismiss()
    }

    private func validateBenchmark() {
        benchmarkError = ""
        guard !benchmarkVersion.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !benchmarkBaselineVersion.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !benchmarkDataset.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            benchmarkError = "版本、基线版本和数据集为必填项"
            return
        }
        do {
            _ = try JSONHelpers.parseObject(benchmarkBeforeJSON)
            _ = try JSONHelpers.parseObject(benchmarkAfterJSON)
            benchmarkConfirmationPresented = true
        } catch {
            benchmarkError = "Before 或 After JSON 格式无效"
        }
    }

    private func recordBenchmark() async {
        do {
            let before = try JSONHelpers.parseObject(benchmarkBeforeJSON)
            let after = try JSONHelpers.parseObject(benchmarkAfterJSON)
            var payload: [String: DynamicJSON] = [
                "action": .string("record_candidate_benchmark"),
                "candidate_id": .string(candidate["candidate_id"].stringValue),
                "workspace_id": .string(candidate["workspace_id"].stringValue),
                "confirmed": .bool(true),
                "version": .string(benchmarkVersion.trimmingCharacters(in: .whitespacesAndNewlines)),
                "baseline_version": .string(benchmarkBaselineVersion.trimmingCharacters(in: .whitespacesAndNewlines)),
                "dataset": .string(benchmarkDataset.trimmingCharacters(in: .whitespacesAndNewlines)),
                "measurement_source": .string(benchmarkSource.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "ios_native_measurement" : benchmarkSource),
                "before": .object(before),
                "after": .object(after)
            ]
            await onSubmit(payload)
            dismiss()
        } catch {
            benchmarkError = "Benchmark JSON 无法解析"
        }
    }

    private func runModelJury() async {
        await onSubmit([
            "action": .string("run_model_jury"),
            "candidate_id": .string(candidate["candidate_id"].stringValue),
            "workspace_id": .string(candidate["workspace_id"].stringValue),
            "confirmed": .bool(true)
        ])
        dismiss()
    }

    private func verifyBuilder() async {
        let payload: [String: DynamicJSON] = [
            "action": .string("verify_builder_artifact"),
            "candidate_id": .string(candidate["candidate_id"].stringValue),
            "artifact_id": .string(builderArtifact["artifact_id"].stringValue),
            "workspace_id": .string(candidate["workspace_id"].stringValue),
            "confirmed": .bool(true)
        ]
        await onSubmit(payload)
        dismiss()
    }

    private func prepareSandboxBundle() async {
        do {
            let specification = try JSONHelpers.parseObject(sandboxBundleJSON)
            sandboxBundleError = ""
            let payload: [String: DynamicJSON] = [
                "action": .string("prepare_sandbox_bundle"),
                "candidate_id": .string(candidate["candidate_id"].stringValue),
                "artifact_id": .string(builderArtifact["artifact_id"].stringValue),
                "workspace_id": .string(candidate["workspace_id"].stringValue),
                "confirmed": .bool(true),
                "sandbox_bundle": .object(specification)
            ]
            await onSubmit(payload)
            dismiss()
        } catch {
            sandboxBundleError = "Bundle JSON 无效：\(error.localizedDescription)"
        }
    }

    private func executeSandbox() async {
        let payload: [String: DynamicJSON] = [
            "action": .string("execute_builder_sandbox"),
            "candidate_id": .string(candidate["candidate_id"].stringValue),
            "artifact_id": .string(builderArtifact["artifact_id"].stringValue),
            "workspace_id": .string(candidate["workspace_id"].stringValue),
            "confirmed": .bool(true),
            "execution_ack": .string("run_untrusted_code_in_isolated_container")
        ]
        await onSubmit(payload)
        dismiss()
    }
}

private struct PoolEditorSheet: View {
    let editor: PoolEditor
    let onSave: ([String: DynamicJSON]) async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var name: String
    @State private var description: String
    @State private var domain: String

    init(editor: PoolEditor, onSave: @escaping ([String: DynamicJSON]) async -> Void) {
        self.editor = editor
        self.onSave = onSave
        _name = State(initialValue: editor.existingName)
        _description = State(initialValue: editor.initialDescription)
        _domain = State(initialValue: editor.initialDomain.isEmpty ? "general" : editor.initialDomain)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("基本信息") {
                    TextField("名称", text: $name)
                        .textInputAutocapitalization(.never)
                    TextField("领域", text: $domain)
                        .textInputAutocapitalization(.never)
                    TextField("描述", text: $description, axis: .vertical)
                        .lineLimit(3...6)
                }
                Section {
                    Text(editor.pool == "skills" ? "Skill 会写入共享池；内置项和其他工作区条目不会显示编辑入口。" : "Workflow 会写入共享池；新建条目先以候选定义保存，运行前仍需桌面端校验。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle(editor.isNew ? "新增\(editor.pool == "skills" ? " Skill" : " Workflow")" : "编辑能力")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") { Task { await save() } }
                        .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
    }

    private func save() async {
        let cleanName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let action = editor.isNew ? "create" : "update"
        var payload: [String: DynamicJSON] = [
            "pool": .string(editor.pool),
            "action": .string(action),
            "name": .string(cleanName),
            "domain": .string(domain.trimmingCharacters(in: .whitespacesAndNewlines)),
            "description": .string(description.trimmingCharacters(in: .whitespacesAndNewlines))
        ]
        if editor.pool == "skills" {
            payload["metadata"] = .object([
                "domain": payload["domain"] ?? .string("general"),
                "description": payload["description"] ?? .string("")
            ])
        } else {
            payload["workflow_name"] = .string(cleanName)
            payload["definition"] = .object([
                "name": .string(cleanName),
                "nodes": .array([]),
                "edges": .array([]),
                "metadata": .object([
                    "domain": payload["domain"] ?? .string("general"),
                    "description": payload["description"] ?? .string("")
                ])
            ])
        }
        await onSave(payload)
        dismiss()
    }
}
