import SwiftUI

struct WorkflowRunDetailView: View {
    @EnvironmentObject private var store: TerminalStore
    let run: WorkflowRunItem
    @State private var selectedNodeID = ""
    @State private var agentID = "ios_terminal"
    @State private var nodePayloadJSON = #"{"submitted_from":"ios_native_terminal"}"#
    @State private var actionSuccess = true

    var body: some View {
        List {
            Section("Run") {
                RowLine(title: currentRun.workflowName, subtitle: currentRun.runId, status: currentRun.status)
                if !currentRun.updatedAt.isEmpty {
                    Text(currentRun.updatedAt)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            Section("Actions") {
                Label("节点由当前 Runtime Host 自动执行", systemImage: "server.rack")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                if nodeIDs.isEmpty {
                    Text("No node actions available")
                        .foregroundStyle(.secondary)
                } else {
                    Picker("Node", selection: $selectedNodeID) {
                        ForEach(nodeIDs, id: \.self) { nodeID in
                            Text(nodeID).tag(nodeID)
                        }
                    }
                    TextField("Agent / actor", text: $agentID)
                        .textInputAutocapitalization(.never)
                    Toggle("Success", isOn: $actionSuccess)
                    TextEditor(text: $nodePayloadJSON)
                        .frame(minHeight: 76)
                        .font(.system(.footnote, design: .monospaced))
                }
                Button("Approve Review") {
                    Task { await workflowAction("approve_review", includeNode: true) }
                }
                .disabled(selectedNodeID.isEmpty)
                Button("Claim Agent Task") {
                    Task { await workflowAction("claim_agent_task", includeNode: true, includeAgent: true) }
                }
                .disabled(selectedNodeID.isEmpty)
                Button("Assign Agent") {
                    Task { await workflowAction("assign_agent", includeNode: true, includeAgent: true) }
                }
                .disabled(selectedNodeID.isEmpty)
                Button("Complete Agent Task") {
                    Task { await completeAgentTask() }
                }
                .disabled(selectedNodeID.isEmpty)
                Button("Signal Node") {
                    Task { await signalNode() }
                }
                .disabled(selectedNodeID.isEmpty)
            }
            Section("Nodes") {
                let nodes = currentRun.raw["nodes"].objectValue
                if nodes.isEmpty {
                    Text("No node state")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(nodes.keys.sorted(), id: \.self) { nodeId in
                        let node = nodes[nodeId] ?? .null
                        RowLine(
                            title: nodeId,
                            subtitle: node["error"].stringValue.isEmpty ? outputSummary(node["outputs"]) : node["error"].stringValue,
                            status: node["status"].stringValue
                        )
                    }
                }
            }
            Section("Events") {
                let events = currentRun.raw["events"].arrayValue.reversed()
                if events.isEmpty {
                    Text("No events")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(Array(events.enumerated()), id: \.offset) { _, event in
                        RowLine(
                            title: event["type"].stringValue,
                            subtitle: event["at"].stringValue,
                            status: event["payload"]["node_id"].stringValue
                        )
                    }
                }
            }
            Section("Raw JSON") {
                ScrollView(.horizontal) {
                    Text(JSONHelpers.pretty(currentRun.raw))
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                }
            }
        }
        .navigationTitle("Run Detail")
        .onAppear {
            if selectedNodeID.isEmpty {
                selectedNodeID = preferredNodeID
            }
        }
        .onChange(of: nodeIDs) { _, ids in
            if !ids.contains(selectedNodeID) {
                selectedNodeID = preferredNodeID
            }
        }
    }

    private var currentRun: WorkflowRunItem {
        store.workflowRuns.first { $0.runId == run.runId } ?? run
    }

    private var nodeIDs: [String] {
        currentRun.raw["nodes"].objectValue.keys.sorted()
    }

    private var preferredNodeID: String {
        let nodes = currentRun.raw["nodes"].objectValue
        return nodeIDs.first { nodeID in
            let status = nodes[nodeID]?["status"].stringValue.lowercased() ?? ""
            return ["running", "waiting", "pending", "blocked"].contains(status)
        } ?? nodeIDs.first ?? ""
    }

    private var resolvedAgentID: String {
        let trimmed = agentID.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "ios_terminal" : trimmed
    }

    private func workflowAction(_ action: String, includeNode: Bool = false, includeAgent: Bool = false) async {
        var payload: [String: DynamicJSON] = [
            "action": .string(action),
            "run_id": .string(currentRun.runId)
        ]
        if includeNode {
            payload["node_id"] = .string(selectedNodeID)
        }
        if includeAgent {
            payload["agent_id"] = .string(resolvedAgentID)
        }
        await store.sendAction(payload, successMessage: "Workflow action sent")
    }

    private func completeAgentTask() async {
        do {
            let outputs = try JSONHelpers.parseObject(nodePayloadJSON)
            await store.sendAction([
                "action": .string("complete_agent_task"),
                "run_id": .string(currentRun.runId),
                "node_id": .string(selectedNodeID),
                "agent_id": .string(resolvedAgentID),
                "success": .bool(actionSuccess),
                "outputs": .object(outputs)
            ], successMessage: "Agent task updated")
        } catch {
            store.statusMessage = error.localizedDescription
        }
    }

    private func signalNode() async {
        do {
            let signalPayload = try JSONHelpers.parseObject(nodePayloadJSON)
            await store.sendAction([
                "action": .string("signal_node"),
                "run_id": .string(currentRun.runId),
                "node_id": .string(selectedNodeID),
                "actor": .string(resolvedAgentID),
                "success": .bool(actionSuccess),
                "signal_payload": .object(signalPayload)
            ], successMessage: "Node signaled")
        } catch {
            store.statusMessage = error.localizedDescription
        }
    }

    private func outputSummary(_ value: DynamicJSON) -> String {
        let object = value.objectValue
        if object.isEmpty {
            return ""
        }
        return object.keys.sorted().prefix(6).joined(separator: ", ")
    }
}
