import SwiftUI

struct MusicControlView: View {
    @EnvironmentObject private var store: TerminalStore
    @State private var volume = 0.8

    private var music: DynamicJSON { store.musicSnapshot }
    private var queue: [DynamicJSON] { music["queue"].arrayValue }
    private var currentIndex: Int { music["current_index"].intValue }
    private var isPlaying: Bool { music["status"].stringValue == "playing" }

    var body: some View {
        List {
            Section("正在播放") {
                if queue.isEmpty {
                    ContentUnavailableView(
                        "播放队列为空",
                        systemImage: "music.note.list",
                        description: Text("在工作素材中上传音频，然后选择立即播放或加入队列。")
                    )
                } else {
                    LabeledContent {
                        Text(music["status"].stringValue)
                            .foregroundStyle(music["controller_online"].boolValue ? FantasyTheme.success : FantasyTheme.warning)
                    } label: {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(music["current_track"]["title"].stringValue)
                            Text("\(time(music["position_seconds"].doubleValue)) / \(time(music["duration_seconds"].doubleValue))")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    ProgressView(
                        value: music["position_seconds"].doubleValue,
                        total: max(1, music["duration_seconds"].doubleValue)
                    )
                    HStack(spacing: 14) {
                        control("backward.fill", "上一首", action: "previous")
                        control(isPlaying ? "pause.fill" : "play.fill", isPlaying ? "暂停" : "播放", action: isPlaying ? "pause" : "resume")
                        control("forward.fill", "下一首", action: "next")
                        control("stop.fill", "停止", action: "stop")
                    }
                    .frame(maxWidth: .infinity)
                }
            }

            Section("音量与循环") {
                HStack {
                    Image(systemName: "speaker.fill")
                    Slider(value: $volume, in: 0...1, step: 0.05) { editing in
                        if !editing {
                            Task {
                                await store.musicAction([
                                    "action": .string("volume"),
                                    "volume": .number(volume)
                                ], successMessage: "音量已更新")
                            }
                        }
                    }
                    Image(systemName: "speaker.wave.3.fill")
                }
                Picker("循环", selection: Binding(
                    get: { music["loop_mode"].stringValue.isEmpty ? "off" : music["loop_mode"].stringValue },
                    set: { mode in
                        Task { await store.musicAction(["action": .string("loop"), "mode": .string(mode)], successMessage: "循环模式已更新") }
                    }
                )) {
                    Text("关闭").tag("off")
                    Text("列表").tag("all")
                    Text("单曲").tag("one")
                }
                .pickerStyle(.segmented)
            }

            Section("播放列表") {
                ForEach(queue.indices, id: \.self) { index in
                    Button {
                        Task { await store.musicAction(["action": .string("select"), "index": .number(Double(index))], successMessage: "已切换曲目") }
                    } label: {
                        HStack {
                            Image(systemName: index == currentIndex ? "speaker.wave.2.fill" : "music.note")
                                .foregroundStyle(index == currentIndex ? FantasyTheme.primary : .secondary)
                                .frame(width: 28)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(queue[index]["title"].stringValue)
                                    .foregroundStyle(.primary)
                                Text(queue[index]["is_remote"].boolValue ? "远程音频" : "桌面文件")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                    .swipeActions {
                        Button("移除", role: .destructive) {
                            Task { await store.musicAction(["action": .string("remove"), "index": .number(Double(index))], successMessage: "已移除曲目") }
                        }
                    }
                }
                if !queue.isEmpty {
                    Button("清空播放列表", role: .destructive) {
                        Task { await store.musicAction(["action": .string("clear")], successMessage: "播放列表已清空") }
                    }
                }
            }

            Section {
                NavigationLink {
                    ArtifactsView()
                } label: {
                    Label("从工作素材添加音频", systemImage: "doc.badge.plus")
                }
            }
        }
        .fantasyCanvas()
        .navigationTitle("音乐播放器")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            Button { Task { await store.refreshMusic(force: true) } } label: { Image(systemName: "arrow.clockwise") }
                .accessibilityLabel("刷新音乐播放器")
        }
        .task {
            await store.refreshMusic(force: true)
            volume = music["volume"].doubleValue == 0 ? 0.8 : music["volume"].doubleValue
        }
        .safeAreaInset(edge: .bottom) { StatusBar(text: store.statusMessage) }
    }

    private func control(_ symbol: String, _ label: String, action: String) -> some View {
        Button {
            Task { await store.musicAction(["action": .string(action)], successMessage: "播放器命令已发送") }
        } label: {
            Image(systemName: symbol)
                .frame(width: 44, height: 44)
        }
        .buttonStyle(.bordered)
        .accessibilityLabel(label)
    }

    private func time(_ seconds: Double) -> String {
        guard seconds.isFinite, seconds >= 0 else { return "0:00" }
        return String(format: "%d:%02d", Int(seconds) / 60, Int(seconds) % 60)
    }
}
