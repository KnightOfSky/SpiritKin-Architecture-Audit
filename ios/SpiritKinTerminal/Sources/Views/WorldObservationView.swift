import ARKit
import SwiftUI

struct WorldObservationView: View {
    @EnvironmentObject private var store: TerminalStore
    @StateObject private var provider = ARKitObservationProvider()
    @State private var startConfirmationPresented = false
    @State private var publishing = false
    @State private var lastPublishedSequence = 0

    private var world: DynamicJSON { store.worldStateSnapshot["world"] }
    private var entities: [DynamicJSON] { world["entities"].arrayValue }

    var body: some View {
        VStack(spacing: 0) {
            ZStack(alignment: .bottomLeading) {
                RealityKitObservationView(provider: provider)
                    .frame(maxWidth: .infinity)
                    .frame(height: 320)
                    .background(Color.black)
                HStack(spacing: 12) {
                    Label(provider.trackingStatus, systemImage: "viewfinder")
                    Label("\(provider.planeCount)", systemImage: "square.3.layers.3d")
                    Label(provider.depthAvailable ? "Depth" : "RGB", systemImage: provider.depthAvailable ? "move.3d" : "camera")
                }
                .font(.caption.weight(.semibold))
                .foregroundStyle(.white)
                .padding(12)
                .background(.black.opacity(0.58))
            }

            List {
                Section("Observation Provider") {
                    LabeledContent("状态", value: provider.isRunning ? "采集中" : "已停止")
                    LabeledContent("World Mapping", value: provider.worldMappingStatus)
                    LabeledContent("LiDAR", value: provider.lidarAvailable ? "可用" : "不可用")
                    LabeledContent("已发布", value: "\(lastPublishedSequence)")
                }
                Section("World State") {
                    RowLine(
                        title: store.workspaceID.isEmpty ? "--" : store.workspaceID,
                        subtitle: "实体 \(world["entity_count"].intValue) · 关系 \(world["relation_count"].intValue)",
                        status: world["current_entity_count"].intValue > 0 ? "current" : "empty"
                    )
                    ForEach(entities.prefix(12).indices, id: \.self) { index in
                        let entity = entities[index]
                        RowLine(
                            title: entity["label"].stringValue.isEmpty ? entity["kind"].stringValue : entity["label"].stringValue,
                            subtitle: "\(entity["kind"].stringValue) · confidence \(String(format: "%.2f", entity["confidence"].doubleValue))",
                            status: entity["status"].stringValue
                        )
                    }
                }
            }
            .fantasyCanvas()
        }
        .navigationTitle("World Observation")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button {
                    Task { await store.refreshWorldState(force: true) }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .accessibilityLabel("刷新 World State")
                Button {
                    if provider.isRunning {
                        provider.stop()
                    } else {
                        startConfirmationPresented = true
                    }
                } label: {
                    Image(systemName: provider.isRunning ? "stop.fill" : "play.fill")
                }
                .accessibilityLabel(provider.isRunning ? "停止空间观察" : "开始空间观察")
                .disabled(!ARWorldTrackingConfiguration.isSupported)
            }
        }
        .task { await store.refreshWorldState() }
        .onDisappear { provider.stop() }
        .onChange(of: provider.sequence) { _, nextSequence in
            guard provider.isRunning,
                  nextSequence > lastPublishedSequence,
                  !publishing,
                  let observation = provider.latestObservation else { return }
            publishing = true
            Task {
                await store.publishObservation(observation)
                lastPublishedSequence = nextSequence
                publishing = false
            }
        }
        .confirmationDialog("开始空间观察？", isPresented: $startConfirmationPresented, titleVisibility: .visible) {
            Button("开始") { provider.start() }
            Button("取消", role: .cancel) {}
        } message: {
            Text("仅同步结构化姿态、平面、定位精度和深度可用性；不会上传 RGB 帧、深度图、点云或录像。")
        }
        .safeAreaInset(edge: .bottom) { StatusBar(text: store.statusMessage) }
    }
}
