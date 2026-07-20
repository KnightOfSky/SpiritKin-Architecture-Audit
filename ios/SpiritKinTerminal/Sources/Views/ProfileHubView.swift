import SwiftUI
import PhotosUI

struct ProfileHubView: View {
    @EnvironmentObject private var store: TerminalStore
    @AppStorage("spiritkin.appearance.mode") private var appearanceMode = SpiritKinAppearance.system.rawValue
    @State private var avatarItem: PhotosPickerItem?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    HStack(spacing: 12) {
                        if let url = store.profileAvatarURL, url.isFileURL {
                            AsyncImage(url: url) { phase in
                                phase.image?.resizable().scaledToFill()
                            } placeholder: { Image(systemName: "sparkles").foregroundStyle(FantasyTheme.primary) }
                            .frame(width: 44, height: 44)
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                        } else {
                            Image(systemName: "sparkles")
                                .font(.title2)
                                .foregroundStyle(FantasyTheme.primary)
                                .frame(width: 44, height: 44)
                                .background(FantasyTheme.surface2, in: RoundedRectangle(cornerRadius: 8))
                        }
                        VStack(alignment: .leading, spacing: 3) {
                            Text("SpiritKin 主控端")
                                .font(.headline)
                            Text(store.workspaceID.isEmpty ? "本地 iOS 主控" : "\(store.workspaceID) · iOS")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                Section("体验") {
                    LabeledContent("默认助手声音", value: "Fairy")
                    Picker("外观", selection: $appearanceMode) {
                        ForEach(SpiritKinAppearance.allCases) { mode in
                            Text(mode.title).tag(mode.rawValue)
                        }
                    }
                    .pickerStyle(.segmented)
                    PhotosPicker(selection: $avatarItem, matching: .images) {
                        Label("从照片选择主控端头像", systemImage: "photo.on.rectangle")
                    }
                    Button("恢复默认 Avatar", role: .destructive) { store.resetAvatarImage() }
                }

                Section("完整主控能力") {
                    NavigationLink {
                        ArtifactsView()
                    } label: {
                        Label("工作素材", systemImage: "photo.on.rectangle.angled")
                    }
                    NavigationLink {
                        MusicControlView()
                    } label: {
                        Label("音乐播放器", systemImage: "music.note.list")
                    }
                    NavigationLink {
                        IOSAutomationView()
                    } label: {
                        Label("iOS 自动化中心", systemImage: "command")
                    }
                    NavigationLink {
                        SettingsView()
                    } label: {
                        Label("连接、通知与开发者设置", systemImage: "gearshape")
                    }
                    NavigationLink {
                        RawJSONView(title: "快照", value: .object([
                            "services": store.snapshot.services,
                            "safety": store.snapshot.safety,
                            "mobile_management": store.snapshot.mobileManagement,
                            "workflows": store.snapshot.workflows,
                            "module_management": store.snapshot.moduleManagement,
                            "model_governance": store.snapshot.modelGovernance,
                            "snapshot_meta": store.snapshot.snapshotMeta
                        ]))
                    } label: {
                        Label("原始快照", systemImage: "terminal")
                    }
                }

                Section("能力边界") {
                    Text("iOS 主控负责对话、审批、工作流、素材与跨端调度。Android 只作为 Bridge 执行端。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .fantasyCanvas()
            .navigationTitle("我的")
            .task(id: avatarItem) {
                guard let avatarItem else { return }
                do {
                    if let data = try await avatarItem.loadTransferable(type: Data.self) {
                        try store.saveAvatarImage(data)
                    }
                } catch {
                    store.statusMessage = "头像读取失败：\(error.localizedDescription)"
                }
            }
        }
    }
}
