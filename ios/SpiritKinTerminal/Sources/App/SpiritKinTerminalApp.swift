import SwiftUI

@main
struct SpiritKinTerminalApp: App {
    @StateObject private var store = TerminalStore()
    @Environment(\.scenePhase) private var scenePhase
    @AppStorage("spiritkin.appearance.mode") private var appearanceMode = SpiritKinAppearance.system.rawValue

    init() {
        BackgroundRefresh.register()
        SpiritKinAppShortcuts.updateAppShortcutParameters()
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(store)
                .preferredColorScheme(SpiritKinAppearance(rawValue: appearanceMode)?.colorScheme)
                .task {
                    await store.refresh()
                }
                .onOpenURL { url in
                    store.applyConfig(from: url)
                }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background {
                BackgroundRefresh.schedule()
            } else if phase == .active {
                Task {
                    await store.importSharedArtifacts()
                    await store.refreshMonitor(force: true)
                }
            }
        }
    }
}
