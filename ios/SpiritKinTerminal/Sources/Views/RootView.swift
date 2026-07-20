import SwiftUI

private enum SpiritKinDestination: String, CaseIterable, Identifiable {
    case conversation
    case workflows
    case devices
    case profile

    var id: Self { self }

    var title: String {
        switch self {
        case .conversation: return "对话"
        case .workflows: return "板块"
        case .devices: return "设备"
        case .profile: return "我的"
        }
    }

    var systemImage: String {
        switch self {
        case .conversation: return "message"
        case .workflows: return "square.grid.2x2"
        case .devices: return "apps.iphone"
        case .profile: return "person.crop.circle"
        }
    }

    @ViewBuilder
    func content(isActive: Bool) -> some View {
        switch self {
        case .conversation: DashboardView(isActive: isActive)
        case .workflows: WorkflowsView()
        case .devices: AndroidBridgeView()
        case .profile: ProfileHubView()
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var store: TerminalStore
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var selection: SpiritKinDestination = .conversation

    var body: some View {
        Group {
            if horizontalSizeClass == .regular {
                NavigationSplitView {
                    List(SpiritKinDestination.allCases, selection: $selection) { destination in
                        Label(destination.title, systemImage: destination.systemImage)
                            .tag(destination)
                    }
                    .navigationTitle("SpiritKin")
                } detail: {
                    selection.content(isActive: true)
                }
            } else {
                TabView(selection: $selection) {
                    ForEach(SpiritKinDestination.allCases) { destination in
                        destination.content(isActive: selection == destination)
                            .tag(destination)
                            .tabItem {
                                Label(destination.title, systemImage: destination.systemImage)
                            }
                    }
                }
            }
        }
        .overlay(alignment: .bottom) {
            if store.isLoading {
                ProgressView()
                    .padding(10)
                    .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8))
                    .padding(.bottom, 8)
            }
        }
        .tint(FantasyTheme.primary)
    }
}
