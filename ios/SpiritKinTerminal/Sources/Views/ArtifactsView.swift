import PhotosUI
import SwiftUI
import UIKit
import UniformTypeIdentifiers

struct ArtifactsView: View {
    @EnvironmentObject private var store: TerminalStore
    @State private var selectedItems: [PhotosPickerItem] = []
    @State private var purpose = "ios_work_image"
    @State private var documentImporterPresented = false

    var body: some View {
        NavigationStack {
            Form {
                Section("上传工作素材") {
                    TextField("用途", text: $purpose)
                    PhotosPicker(selection: $selectedItems, matching: .images) {
                        Label("选择照片", systemImage: "photo.on.rectangle.angled")
                    }
                    Button {
                        documentImporterPresented = true
                    } label: {
                        Label("选择文件", systemImage: "doc.badge.plus")
                    }
                    Text("已选择 \(selectedItems.count) 张")
                        .foregroundStyle(.secondary)
                    Button("上传") {
                        Task {
                            await store.uploadPhotos(selectedItems, purpose: purpose)
                            selectedItems.removeAll()
                        }
                    }
                    .disabled(selectedItems.isEmpty)
                    Button("清理过期素材") {
                        Task {
                            await store.sendAction([
                                "action": .string("cleanup_mobile_artifacts"),
                                "expired_only": .bool(true)
                            ], successMessage: "已请求清理")
                        }
                    }
                }
                Section("素材概览") {
                    let artifacts = store.ecommerceSnapshot["artifacts"]
                    RowLine(
                        title: "\(artifacts["count"].intValue) 个素材组",
                        subtitle: "总大小 \(ByteCountFormatter.string(fromByteCount: Int64(artifacts["total_size_bytes"].intValue), countStyle: .file))",
                        status: "可用"
                    )
                }
                Section("最近工作素材") {
                    if store.recentArtifacts.isEmpty {
                        Text("暂无最近素材")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(store.recentArtifacts) { artifact in
                            NavigationLink {
                                ArtifactDetailView(artifact: artifact)
                            } label: {
                                ArtifactRow(artifact: artifact)
                            }
                        }
                    }
                }
            }
            .fantasyCanvas()
            .navigationTitle("素材")
            .safeAreaInset(edge: .bottom) {
                StatusBar(text: store.statusMessage)
            }
            .fileImporter(
                isPresented: $documentImporterPresented,
                allowedContentTypes: [.item],
                allowsMultipleSelection: true
            ) { result in
                switch result {
                case .success(let urls):
                    Task { await store.uploadDocuments(urls, purpose: purpose) }
                case .failure(let error):
                    store.statusMessage = "文件选择失败：\(error.localizedDescription)"
                }
            }
            .task { await store.refreshEcommerce() }
        }
    }
}

private struct ArtifactRow: View {
    let artifact: MobileArtifactItem

    var body: some View {
        RowLine(
            title: artifact.name.isEmpty ? artifact.artifactID : artifact.name,
            subtitle: "\(artifact.purpose) · \(artifact.source) · \(artifact.sizeBytes) bytes",
            status: artifact.mimeType.isEmpty ? "素材" : artifact.mimeType
        )
    }
}

private struct ArtifactDetailView: View {
    @EnvironmentObject private var store: TerminalStore
    let artifact: MobileArtifactItem
    @State private var imageData: Data?

    var body: some View {
        List {
            Section("素材") {
                RowLine(title: artifact.name, subtitle: artifact.artifactID, status: artifact.mimeType)
                if !artifact.createdAt.isEmpty {
                    Text(artifact.createdAt)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            if artifact.isImage {
                Section("预览") {
                    if let imageData, let image = UIImage(data: imageData) {
                        Image(uiImage: image)
                            .resizable()
                            .scaledToFit()
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                    } else {
                        ProgressView()
                    }
                    .frame(maxWidth: .infinity)
                }
            }
            if artifact.isAudio {
                Section("桌面播放器") {
                    Button("立即播放", systemImage: "play.fill") {
                        Task {
                            await store.musicAction([
                                "action": .string("play_artifact"),
                                "artifact_id": .string(artifact.artifactID),
                                "file_index": .number(Double(artifact.fileIndex))
                            ], successMessage: "已发送到桌面播放器")
                        }
                    }
                    Button("加入队列", systemImage: "text.badge.plus") {
                        Task {
                            await store.musicAction([
                                "action": .string("queue_artifact"),
                                "artifact_id": .string(artifact.artifactID),
                                "file_index": .number(Double(artifact.fileIndex))
                            ], successMessage: "已加入桌面播放队列")
                        }
                    }
                }
            }
            Section("原始数据") {
                ScrollView(.horizontal) {
                    Text(JSONHelpers.pretty(artifact.raw))
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                }
            }
        }
        .navigationTitle("素材")
        .task {
            guard artifact.isImage, imageData == nil else { return }
            imageData = try? await store.artifactData(artifact)
        }
    }
}
