import SwiftUI

struct DashboardView: View {
    @EnvironmentObject private var store: TerminalStore
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @AppStorage("spiritkin.appearance.mode") private var appearanceMode = SpiritKinAppearance.system.rawValue
    var isActive: Bool = true
    @State private var hardStopConfirmation = ""
    @State private var sessionsPresented = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                AvatarStageView(url: store.avatarURL, isActive: isActive)
                    .frame(height: horizontalSizeClass == .regular ? 360 : 240)
                ConversationPanel()
                Divider()
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 138), spacing: 10)], spacing: 10) {
                        ForEach(store.metrics) { metric in
                            MetricCard(metric: metric)
                        }
                    }
                    SectionBlock(title: "Services") {
                        ForEach(store.snapshot.services["services"].arrayValue.indices, id: \.self) { index in
                            let service = store.snapshot.services["services"].arrayValue[index]
                            RowLine(
                                title: service["label"].stringValue.isEmpty ? service["service_id"].stringValue : service["label"].stringValue,
                                subtitle: "\(service["service_id"].stringValue) · \(service["port"].stringValue)",
                                status: service["running"].boolValue ? "running" : "stopped"
                            )
                        }
                    }
                    SectionBlock(title: "Modules") {
                        Text("Ready \(store.snapshot.moduleManagement["ready_count"].intValue) / \(store.snapshot.moduleManagement["module_count"].intValue)")
                            .font(.body)
                        Text("Attention \(store.snapshot.moduleManagement["attention_count"].intValue) · Blocked \(store.snapshot.moduleManagement["blocked_count"].intValue)")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                        NavigationLink("管理能力模块") { CapabilityModulesView() }
                        NavigationLink("查看 Skill / Workflow 池") { CapabilityPoolsView() }
                    }
                    SectionBlock(title: "Models") {
                        RowLine(
                            title: store.snapshot.modelGovernance["hardware_class"].stringValue.isEmpty ? "local policy" : store.snapshot.modelGovernance["hardware_class"].stringValue,
                            subtitle: "\(store.snapshot.modelGovernance["default_mode"].stringValue) · roles \(store.snapshot.modelGovernance["role_count"].intValue) · adapters \(store.snapshot.modelGovernance["adapter_count"].intValue)",
                            status: store.snapshot.modelGovernance["status"].stringValue.isEmpty ? "ready" : store.snapshot.modelGovernance["status"].stringValue
                        )
                        Text("Benchmark \(store.snapshot.modelGovernance["scheduler_benchmark"]["status"].stringValue) · cases \(store.snapshot.modelGovernance["scheduler_benchmark"]["case_count"].intValue)")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                        ForEach(store.snapshot.modelGovernance["local_roles"].arrayValue.prefix(2).indices, id: \.self) { index in
                            let role = store.snapshot.modelGovernance["local_roles"].arrayValue[index]
                            RowLine(
                                title: role["label"].stringValue,
                                subtitle: "\(role["model_id"].stringValue) · \(role["quantization_profile"].stringValue)",
                                status: role["status"].stringValue.isEmpty
                                    ? (store.snapshot.modelGovernance["status"].stringValue.isEmpty ? "unknown" : store.snapshot.modelGovernance["status"].stringValue)
                                    : role["status"].stringValue
                            )
                        }
                        ForEach(store.snapshot.modelGovernance["adapters"].arrayValue.prefix(2).indices, id: \.self) { index in
                            let adapter = store.snapshot.modelGovernance["adapters"].arrayValue[index]
                            RowLine(
                                title: adapter["label"].stringValue,
                                subtitle: "\(adapter["model_id"].stringValue) · \(adapter["review_state"].stringValue)",
                                status: adapter["status"].stringValue
                            )
                        }
                    }
                    SectionBlock(title: "Safety") {
                        RowLine(
                            title: store.snapshot.safety["mode"].stringValue.isEmpty ? "normal" : store.snapshot.safety["mode"].stringValue,
                            subtitle: store.snapshot.safety["reason"].stringValue,
                            status: store.snapshot.safety["active"].boolValue ? "stop" : "normal"
                        )
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Button("Soft Stop") {
                                    Task {
                                        await store.sendAction([
                                            "action": .string("panic_stop"),
                                            "mode": .string("soft_stop"),
                                            "reason": .string("iOS native terminal soft stop")
                                        ], successMessage: "Safety stop requested")
                                    }
                                }
                                Button("Hard Stop", role: .destructive) {
                                    Task { await store.hardStop() }
                                }
                            }
                            if store.snapshot.safety["resume_confirmation_required"].boolValue {
                                TextField("Type \(store.snapshot.safety["resume_confirmation_text"].stringValue)", text: $hardStopConfirmation)
                                    .textInputAutocapitalization(.characters)
                            }
                            Button("Resume") {
                                Task { await store.resumeSafety(confirmation: hardStopConfirmation) }
                            }
                        }
                        .buttonStyle(.bordered)
                    }
                    }
                    .padding()
                }
            }
            .fantasyCanvas()
            .navigationTitle(store.activeConversationTitle)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        sessionsPresented = true
                    } label: {
                        Image(systemName: "clock.arrow.circlepath")
                    }
                    .accessibilityLabel("管理会话")
                }
                ToolbarItemGroup(placement: .topBarTrailing) {
                    Button {
                        store.createConversation()
                    } label: {
                        Image(systemName: "square.and.pencil")
                    }
                    .accessibilityLabel("新建会话")
                    Menu {
                        Picker("外观", selection: $appearanceMode) {
                            ForEach(SpiritKinAppearance.allCases) { mode in
                                Label(mode.title, systemImage: mode == .system ? "circle.lefthalf.filled" : (mode == .light ? "sun.max" : "moon"))
                                    .tag(mode.rawValue)
                            }
                        }
                    } label: {
                        Image(systemName: "circle.lefthalf.filled")
                    }
                    .accessibilityLabel("切换外观")
                Button {
                    Task { await store.refresh(force: true) }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .accessibilityLabel("刷新")
                }
            }
            .sheet(isPresented: $sessionsPresented) {
                ConversationSessionsView()
                    .environmentObject(store)
            }
            .safeAreaInset(edge: .bottom) {
                StatusBar(text: store.statusMessage)
            }
        }
    }
}
