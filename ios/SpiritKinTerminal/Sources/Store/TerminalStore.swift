import Foundation
import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

@MainActor
final class TerminalStore: ObservableObject {
    @Published var baseURLString: String {
        didSet { UserDefaults.standard.set(baseURLString, forKey: "spiritkin.ios.baseURL") }
    }
    @Published var token: String {
        didSet { KeychainStore.write(token, key: "ios.token") }
    }
    @Published var snapshot = TerminalSnapshot.empty
    @Published var isLoading = false
    @Published var statusMessage = "Ready"
    @Published var conversationDraft = ""
    @Published var conversationMessages: [TerminalConversationMessage] = [
        TerminalConversationMessage(
            role: .assistant,
            text: "主控端已启动。连接桌面 Runtime 后即可发送目标，需要确认的步骤会留在对话中。"
        )
    ]
    @Published var conversationSessions: [DesktopSessionItem] = []
    @Published var activeConversationSessionID = ""
    @Published var conversationRevision = 0
    @Published var selectedWorkflow = "ecommerce.auto_listing.v1"
    @Published var workspaceID: String {
        didSet { UserDefaults.standard.set(workspaceID, forKey: "spiritkin.ios.workspaceID") }
    }
    @Published var capabilitySnapshot = IOSCapabilitySnapshot(capabilities: [], enabledCount: 0, capabilityCount: 0)
    @Published var domainSnapshot: DynamicJSON = .object([:])
    @Published var poolSnapshot = IOSPoolSnapshot(skills: .object([:]), workflows: .object([:]))
    @Published var resourceSnapshot: DynamicJSON = .object([:])
    @Published var monitorSnapshot: DynamicJSON = .object([:])
    @Published var growthSnapshot: DynamicJSON = .object([:])
    @Published var runtimeHostSnapshot: DynamicJSON = .object([:])
    @Published var worldStateSnapshot: DynamicJSON = .object([:])
    @Published var ecommerceSnapshot: DynamicJSON = .object([:])
    @Published var musicSnapshot: DynamicJSON = .object([:])
    @Published var channelsSnapshot: DynamicJSON = .object([:])
    @Published var shortcutCatalog: [DynamicJSON] = []
    @Published var remoteWorkerPairing: DynamicJSON = .object([:])
    @Published var customAvatarURL: String {
        didSet { UserDefaults.standard.set(customAvatarURL, forKey: "spiritkin.ios.avatarURL") }
    }
    private var refreshInFlight = false
    private var sessionsRefreshInFlight = false
    private var domainsRefreshInFlight = false
    private var ecommerceRefreshInFlight = false
    private var musicRefreshInFlight = false
    private var channelsRefreshInFlight = false
    private var capabilitiesRefreshInFlight = false
    private var poolsRefreshInFlight = false
    private var resourcesRefreshInFlight = false
    private var monitorRefreshInFlight = false
    private var growthRefreshInFlight = false
    private var runtimeHostRefreshInFlight = false
    private var worldStateRefreshInFlight = false
    private var shortcutsRefreshInFlight = false
    private var lastRefreshFinishedAt: Date?
    private let refreshThrottleInterval: TimeInterval = 1.5
    private var endpointFreshUntil: [String: Date] = [:]
    private var conversationJobTasks: [String: Task<Void, Never>] = [:]
    private var conversationJobSessions: [String: String] = [:]
    private let terminalID: String

    init() {
        let savedURL = UserDefaults.standard.string(forKey: "spiritkin.ios.baseURL") ?? "http://127.0.0.1:8791"
        if let parsed = URLComponents(string: savedURL), parsed.host == "127.0.0.1", parsed.port == 8792 {
            baseURLString = "http://127.0.0.1:8791"
        } else {
            baseURLString = savedURL
        }
        let migratedToken = KeychainStore.read("ios.token") ?? UserDefaults.standard.string(forKey: "spiritkin.ios.token") ?? ""
        token = migratedToken
        KeychainStore.write(migratedToken, key: "ios.token")
        UserDefaults.standard.removeObject(forKey: "spiritkin.ios.token")
        workspaceID = UserDefaults.standard.string(forKey: "spiritkin.ios.workspaceID") ?? ""
        customAvatarURL = UserDefaults.standard.string(forKey: "spiritkin.ios.avatarURL") ?? ""
        if let savedTerminalID = UserDefaults.standard.string(forKey: "spiritkin.ios.terminalID"), !savedTerminalID.isEmpty {
            terminalID = savedTerminalID
        } else {
            let value = "ios-native-\(UUID().uuidString.lowercased())"
            UserDefaults.standard.set(value, forKey: "spiritkin.ios.terminalID")
            terminalID = value
        }
    }

    var api: TerminalAPI? {
        guard let url = URL(string: baseURLString.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            return nil
        }
        return TerminalAPI(baseURL: url, token: token, workspaceID: workspaceID)
    }

    var profileAvatarURL: URL? {
        let raw = customAvatarURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty,
              let custom = URL(string: raw),
              ["http", "https", "file"].contains(custom.scheme?.lowercased() ?? "") else { return nil }
        if custom.isFileURL { return custom }
        let loopback = custom.host == "127.0.0.1" || custom.host == "localhost" || custom.host == "::1" || custom.host == "[::1]"
        if custom.scheme?.lowercased() == "http" && !loopback { return nil }
        return custom
    }

    var avatarURL: URL? {
        let raw = baseURLString.trimmingCharacters(in: .whitespacesAndNewlines)
        guard var components = URLComponents(string: raw), components.scheme?.hasPrefix("http") == true else {
            return nil
        }
        if components.port == 8791 {
            // 8791 is the control API; the shared 3D stage is served by the
            // static iOS/Web surface on the adjacent 8792 port.
            components.port = 8792
        }
        components.path = "/frontend/avatar_3d.html"
        components.queryItems = [
            URLQueryItem(name: "embed", value: "1"),
            URLQueryItem(name: "float", value: "1")
        ]
        return components.url
    }

    func saveAvatarImage(_ data: Data) throws {
        let directory = try FileManager.default.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ).appendingPathComponent("SpiritKin", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let fileURL = directory.appendingPathComponent("controller-avatar.jpg")
        try data.write(to: fileURL, options: .atomic)
        customAvatarURL = fileURL.absoluteString
        statusMessage = "主控端头像已从照片更新"
    }

    func resetAvatarImage() {
        if let url = URL(string: customAvatarURL), url.isFileURL {
            try? FileManager.default.removeItem(at: url)
        }
        customAvatarURL = ""
        statusMessage = "已恢复默认 Avatar"
    }

    var ecommerceTerminalURL: URL? {
        let raw = baseURLString.trimmingCharacters(in: .whitespacesAndNewlines)
        guard var components = URLComponents(string: raw), components.scheme?.hasPrefix("http") == true else {
            return nil
        }
        components.path = "/ios/terminal"
        var queryItems: [URLQueryItem] = []
        if !workspaceID.isEmpty {
            queryItems.append(URLQueryItem(name: "workspace_id", value: workspaceID))
        }
        components.queryItems = queryItems.isEmpty ? nil : queryItems
        return components.url
    }

    var activeConversationTitle: String {
        conversationSessions.first(where: { $0.id == activeConversationSessionID })?.title ?? "对话"
    }

    func refresh(force: Bool = false) async {
        guard let api else {
            statusMessage = "Invalid Base URL"
            return
        }
        guard !refreshInFlight else { return }
        if !force, let lastRefreshFinishedAt,
           Date().timeIntervalSince(lastRefreshFinishedAt) < refreshThrottleInterval {
            return
        }
        refreshInFlight = true
        isLoading = true
        defer {
            refreshInFlight = false
            isLoading = false
            lastRefreshFinishedAt = Date()
        }
        do {
            // These snapshots are independent. Fetching them together removes
            // the serial 7-request delay that made tab changes feel blocked.
            async let nextSnapshot = api.snapshot(forceRefresh: force)
            async let nextSessions = api.sessions()
            async let nextCapabilities = api.capabilities()
            async let nextDomains = api.domains()
            async let nextPools = api.pools()
            async let nextGrowth = api.growth()

            snapshot = try await nextSnapshot
            let sessions = try await nextSessions
            conversationRevision = sessions.revision
            conversationSessions = sessions.sessions
            activeConversationSessionID = sessions.activeSessionID
            applyActiveConversation()
            markEndpointFresh("sessions")
            capabilitySnapshot = try await nextCapabilities
            markEndpointFresh("capabilities")
            domainSnapshot = try await nextDomains
            markEndpointFresh("domains")
            poolSnapshot = try await nextPools
            markEndpointFresh("pools")
            growthSnapshot = try await nextGrowth
            markEndpointFresh("growth")
            if let first = workflowDefinitions.first, !workflowDefinitions.contains(where: { $0.name == selectedWorkflow }) {
                selectedWorkflow = first.name
            }
            statusMessage = "Snapshot \(snapshot.snapshotMeta["cache"].stringValue) \(snapshot.snapshotMeta["duration_ms"].stringValue)ms"
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func applyConfig(from url: URL) {
        applyConfig(TerminalConfig.parse(url: url))
    }

    func applyConfig(from text: String) {
        do {
            try applyConfig(TerminalConfig.parse(text))
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    private func applyConfig(_ config: TerminalConfig) {
        let resolvedBaseURL = config.baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !resolvedBaseURL.isEmpty else {
            statusMessage = TerminalConfigError.missingBaseURL.localizedDescription
            return
        }
        guard let newURL = URL(string: resolvedBaseURL), ["http", "https"].contains(newURL.scheme?.lowercased() ?? "") else {
            statusMessage = "主控地址必须使用 http 或 https"
            return
        }
        let isLoopback = newURL.host == "127.0.0.1" || newURL.host == "localhost" || newURL.host == "::1" || newURL.host == "[::1]"
        if newURL.scheme?.lowercased() == "http" && !isLoopback {
            statusMessage = "远程主控必须使用 HTTPS；HTTP 仅允许本机回环地址"
            return
        }
        let oldComponents = URLComponents(string: baseURLString)
        let oldOrigin = oldComponents?.host.map { "\($0):\(oldComponents?.port ?? (oldComponents?.scheme == "https" ? 443 : 80))" }
        let newOrigin = "\(newURL.host ?? ""):\(newURL.port ?? (newURL.scheme == "https" ? 443 : 80))"
        baseURLString = resolvedBaseURL
        if !config.pairingToken.isEmpty {
            token = ""
        } else if !config.token.isEmpty {
            token = config.token
        } else if oldOrigin != nil && oldOrigin != newOrigin {
            token = ""
        }
        if !config.workspaceID.isEmpty {
            workspaceID = config.workspaceID
        }
        statusMessage = "Imported iOS terminal config"
        if !config.pairingToken.isEmpty {
            Task { await bindPairingToken(config.pairingToken) }
        } else {
            Task { await refresh(force: true) }
        }
    }

    func bindPairingToken(_ rawPairingToken: String) async {
        let pairingToken = rawPairingToken.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !pairingToken.isEmpty, let api else {
            statusMessage = pairingToken.isEmpty ? "请输入 iOS 配对码" : "Invalid Base URL"
            return
        }
        isLoading = true
        defer { isLoading = false }
        do {
            let response = try await api.pairControl(pairingToken: pairingToken)
            let binding = response["binding"]
            let boundToken = binding["token"].stringValue
            guard !boundToken.isEmpty else {
                statusMessage = "配对响应缺少访问令牌"
                return
            }
            token = boundToken
            if !binding["workspace_id"].stringValue.isEmpty {
                workspaceID = binding["workspace_id"].stringValue
            }
            statusMessage = "iOS 主控已完成配对"
            await refresh(force: true)
        } catch {
            statusMessage = "iOS 配对失败：\(error.localizedDescription)"
        }
    }

    func createRemoteWorkerPairing() async {
        guard let api else {
            statusMessage = "Invalid Base URL"
            return
        }
        isLoading = true
        defer { isLoading = false }
        do {
            remoteWorkerPairing = try await api.createPairing(deviceRole: "remote_worker", ttlMinutes: 10)
            statusMessage = "Remote Worker 一次性配对码已生成"
        } catch {
            statusMessage = "Remote Worker 配对失败：\(error.localizedDescription)"
        }
    }

    func cancelRemoteWorkerPairing() async {
        let tokenID = remoteWorkerPairing["token_id"].stringValue
        guard !tokenID.isEmpty, let api else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            _ = try await api.action([
                "action": .string("cancel_pairing_token"),
                "workspace_id": .string(remoteWorkerPairing["workspace_id"].stringValue.isEmpty ? workspaceID : remoteWorkerPairing["workspace_id"].stringValue),
                "token_id": .string(tokenID)
            ])
            remoteWorkerPairing = .object([:])
            statusMessage = "Remote Worker 配对码已取消"
        } catch {
            statusMessage = "取消配对码失败：\(error.localizedDescription)"
        }
    }

    func refreshShortcutCatalog(force: Bool = false) async {
        guard let api else { return }
        guard !shortcutsRefreshInFlight else { return }
        guard beginEndpointRefresh("shortcuts", force: force) else { return }
        shortcutsRefreshInFlight = true
        defer { shortcutsRefreshInFlight = false }
        do {
            shortcutCatalog = try await api.shortcutCatalog()
            markEndpointFresh("shortcuts")
        } catch {
            statusMessage = "快捷指令目录同步失败：\(error.localizedDescription)"
        }
    }

    func sendAction(_ payload: [String: DynamicJSON], successMessage: String) async {
        guard let api else {
            statusMessage = "Invalid Base URL"
            return
        }
        isLoading = true
        defer { isLoading = false }
        do {
            let response = try await api.action(payload)
            if let next = response.iosControl {
                snapshot = next
            } else {
                await refresh(force: true)
            }
            statusMessage = response.message ?? successMessage
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func sendConversationMessage() async {
        let text = conversationDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, let api else { return }
        let sessionID = activeConversationSessionID
        conversationDraft = ""
        conversationMessages.append(TerminalConversationMessage(role: .user, text: text))
        isLoading = true
        defer { isLoading = false }
        do {
            // Persist the outgoing message before entering the model request so
            // iOS process suspension cannot lose the user's side of the turn.
            await persistConversations()
            let submission = try await api.askSpirit(text, sessionID: sessionID)
            switch submission.status {
            case .completed(let reply):
                appendConversationMessage(.assistant, text: reply, sessionID: sessionID)
                statusMessage = "Ask Spirit completed"
            case .failed(let error):
                appendConversationMessage(.system, text: "桌面 Runtime 任务失败：\(error)", sessionID: sessionID)
                statusMessage = "Ask Spirit failed"
            case .pending:
                let placeholderID = "job-\(submission.jobID)"
                appendConversationMessage(
                    .system,
                    text: "请求已进入桌面 Runtime 队列，iOS 将在后台继续同步结果。",
                    sessionID: sessionID,
                    messageID: placeholderID
                )
                statusMessage = "请求已提交，后台等待桌面 Runtime"
                continueSpiritJob(submission.jobID, sessionID: sessionID, placeholderID: placeholderID)
            }
            await persistConversations()
        } catch {
            appendConversationMessage(.system, text: "发送失败：\(error.localizedDescription)", sessionID: sessionID)
            statusMessage = error.localizedDescription
            await persistConversations()
        }
    }

    func refreshConversations(force: Bool = false) async {
        guard let api else { return }
        guard !sessionsRefreshInFlight else { return }
        guard beginEndpointRefresh("sessions", force: force) else { return }
        sessionsRefreshInFlight = true
        defer { sessionsRefreshInFlight = false }
        do {
            let state = try await api.sessions()
            conversationRevision = state.revision
            conversationSessions = state.sessions
            activeConversationSessionID = state.activeSessionID
            applyActiveConversation()
            markEndpointFresh("sessions")
        } catch {
            if conversationSessions.isEmpty {
                createConversation(persist: false)
            }
            statusMessage = "会话同步失败：\(error.localizedDescription)"
        }
    }

    func refreshCapabilities(force: Bool = false) async {
        guard let api else { return }
        guard !capabilitiesRefreshInFlight else { return }
        guard beginEndpointRefresh("capabilities", force: force) else { return }
        capabilitiesRefreshInFlight = true
        defer { capabilitiesRefreshInFlight = false }
        do {
            capabilitySnapshot = try await api.capabilities()
            markEndpointFresh("capabilities")
        } catch { statusMessage = "能力同步失败：\(error.localizedDescription)" }
    }

    func toggleCapability(_ item: IOSCapabilityItem, enabled: Bool) async {
        guard !item.locked else { return }
        guard let api else { return }
        do {
            capabilitySnapshot = try await api.updateCapability(item.capabilityID, enabled: enabled)
            markEndpointFresh("capabilities")
        } catch { statusMessage = "能力开关失败：\(error.localizedDescription)" }
    }

    func refreshDomains(force: Bool = false) async {
        guard let api else { return }
        guard !domainsRefreshInFlight else { return }
        guard beginEndpointRefresh("domains", force: force) else { return }
        domainsRefreshInFlight = true
        defer { domainsRefreshInFlight = false }
        do {
            domainSnapshot = try await api.domains()
            markEndpointFresh("domains")
        } catch { statusMessage = "领域同步失败：\(error.localizedDescription)" }
    }

    func domainAction(_ payload: [String: DynamicJSON], successMessage: String) async {
        guard let api else { return }
        do {
            domainSnapshot = try await api.domainsAction(payload)
            markEndpointFresh("domains")
            statusMessage = successMessage
        } catch {
            statusMessage = "领域操作失败：\(error.localizedDescription)"
        }
    }

    func refreshPools(force: Bool = false) async {
        guard let api else { return }
        guard !poolsRefreshInFlight else { return }
        guard beginEndpointRefresh("pools", force: force) else { return }
        poolsRefreshInFlight = true
        defer { poolsRefreshInFlight = false }
        do {
            poolSnapshot = try await api.pools()
            markEndpointFresh("pools")
        } catch { statusMessage = "Skill/Workflow 池同步失败：\(error.localizedDescription)" }
    }

    func poolAction(_ payload: [String: DynamicJSON], successMessage: String) async {
        guard let api else { return }
        do {
            poolSnapshot = try await api.poolAction(payload)
            markEndpointFresh("pools")
            statusMessage = successMessage
        } catch {
            statusMessage = "能力池操作失败：\(error.localizedDescription)"
        }
    }

    func refreshResources(force: Bool = false) async {
        guard let api else { return }
        guard !resourcesRefreshInFlight else { return }
        guard beginEndpointRefresh("resources", force: force) else { return }
        resourcesRefreshInFlight = true
        defer { resourcesRefreshInFlight = false }
        do {
            resourceSnapshot = try await api.resources()
            markEndpointFresh("resources")
        } catch {
            statusMessage = "Resource 同步失败：\(error.localizedDescription)"
        }
    }

    func resourceAction(_ payload: [String: DynamicJSON], successMessage: String) async {
        guard let api else { return }
        do {
            resourceSnapshot = try await api.resourceAction(payload)
            markEndpointFresh("resources")
            statusMessage = successMessage
        } catch {
            statusMessage = "Resource 操作失败：\(error.localizedDescription)"
        }
    }

    func refreshMonitor(force: Bool = false) async {
        guard let api else { return }
        guard !monitorRefreshInFlight else { return }
        guard beginEndpointRefresh("monitor", force: force) else { return }
        monitorRefreshInFlight = true
        defer { monitorRefreshInFlight = false }
        do {
            try await api.heartbeat(terminalID: terminalID)
            monitorSnapshot = try await api.monitor()
            markEndpointFresh("monitor")
        } catch {
            statusMessage = "运行监控同步失败：\(error.localizedDescription)"
        }
    }

    func monitorAction(_ payload: [String: DynamicJSON], successMessage: String) async {
        guard let api else { return }
        do {
            monitorSnapshot = try await api.monitorAction(payload)
            markEndpointFresh("monitor")
            statusMessage = successMessage
        } catch {
            statusMessage = "修复操作失败：\(error.localizedDescription)"
        }
    }

    func refreshGrowth(force: Bool = false) async {
        guard let api else { return }
        guard !growthRefreshInFlight else { return }
        guard beginEndpointRefresh("growth", force: force) else { return }
        growthRefreshInFlight = true
        defer { growthRefreshInFlight = false }
        do {
            growthSnapshot = try await api.growth()
            markEndpointFresh("growth")
        } catch { statusMessage = "能力成长同步失败：\(error.localizedDescription)" }
    }

    func growthAction(_ payload: [String: DynamicJSON], successMessage: String) async {
        guard let api else { return }
        do {
            growthSnapshot = try await api.growthAction(payload)
            markEndpointFresh("growth")
            statusMessage = successMessage
        } catch {
            statusMessage = "Growth 治理操作失败：\(error.localizedDescription)"
        }
    }

    func refreshRuntimeHosts(force: Bool = false) async {
        guard let api else { return }
        guard !runtimeHostRefreshInFlight else { return }
        guard beginEndpointRefresh("runtime-host", force: force) else { return }
        runtimeHostRefreshInFlight = true
        defer { runtimeHostRefreshInFlight = false }
        do {
            runtimeHostSnapshot = try await api.runtimeHost()
            markEndpointFresh("runtime-host")
        } catch {
            statusMessage = "Runtime Host 同步失败：\(error.localizedDescription)"
        }
    }

    func runtimeHostAction(_ payload: [String: DynamicJSON], successMessage: String) async {
        guard let api else { return }
        do {
            runtimeHostSnapshot = try await api.runtimeHostAction(payload)
            markEndpointFresh("runtime-host")
            statusMessage = successMessage
        } catch {
            statusMessage = "Runtime Host 操作失败：\(error.localizedDescription)"
        }
    }

    func refreshWorldState(force: Bool = false) async {
        guard let api else { return }
        guard !worldStateRefreshInFlight else { return }
        guard beginEndpointRefresh("world", force: force) else { return }
        worldStateRefreshInFlight = true
        defer { worldStateRefreshInFlight = false }
        do {
            worldStateSnapshot = try await api.worldState()
            markEndpointFresh("world")
        } catch {
            statusMessage = "World State 同步失败：\(error.localizedDescription)"
        }
    }

    func publishObservation(_ observation: DynamicJSON) async {
        guard let api else { return }
        do {
            worldStateSnapshot = try await api.publishObservation(observation)
            markEndpointFresh("world")
            statusMessage = "空间观察已同步"
        } catch {
            statusMessage = "空间观察同步失败：\(error.localizedDescription)"
        }
    }

    func refreshEcommerce(force: Bool = false) async {
        guard let api else { return }
        guard !ecommerceRefreshInFlight else { return }
        guard beginEndpointRefresh("ecommerce", force: force) else { return }
        ecommerceRefreshInFlight = true
        defer { ecommerceRefreshInFlight = false }
        do {
            ecommerceSnapshot = try await api.ecommerce()
            markEndpointFresh("ecommerce")
            monitorSnapshot = ecommerceSnapshot["monitor"]
            statusMessage = "电商运营数据已同步"
        } catch {
            statusMessage = "电商运营同步失败：\(error.localizedDescription)"
        }
    }

    func ecommerceAction(_ payload: [String: DynamicJSON], successMessage: String) async {
        guard let api else { return }
        do {
            ecommerceSnapshot = try await api.ecommerceAction(payload)
            markEndpointFresh("ecommerce")
            statusMessage = successMessage
        } catch {
            statusMessage = "电商操作失败：\(error.localizedDescription)"
        }
    }

    func refreshMusic(force: Bool = false) async {
        guard let api else { return }
        guard !musicRefreshInFlight else { return }
        guard beginEndpointRefresh("music", force: force) else { return }
        musicRefreshInFlight = true
        defer { musicRefreshInFlight = false }
        do {
            musicSnapshot = try await api.music()
            markEndpointFresh("music")
        } catch {
            statusMessage = "音乐播放器同步失败：\(error.localizedDescription)"
        }
    }

    func musicAction(_ payload: [String: DynamicJSON], successMessage: String) async {
        guard let api else { return }
        do {
            musicSnapshot = try await api.musicAction(payload)
            endpointFreshUntil["music"] = .distantPast
            statusMessage = successMessage
            try? await Task.sleep(for: .milliseconds(650))
            await refreshMusic(force: true)
        } catch {
            statusMessage = "音乐操作失败：\(error.localizedDescription)"
        }
    }

    func refreshChannels(force: Bool = false) async {
        guard let api else { return }
        guard !channelsRefreshInFlight else { return }
        guard beginEndpointRefresh("channels", force: force) else { return }
        channelsRefreshInFlight = true
        defer { channelsRefreshInFlight = false }
        do {
            channelsSnapshot = try await api.channels()
            markEndpointFresh("channels")
        } catch {
            statusMessage = "消息通道同步失败：\(error.localizedDescription)"
        }
    }

    private func beginEndpointRefresh(_ key: String, force: Bool) -> Bool {
        let now = Date()
        if !force, endpointFreshUntil[key, default: .distantPast] > now {
            return false
        }
        endpointFreshUntil[key] = now.addingTimeInterval(refreshThrottleInterval)
        return true
    }

    private func markEndpointFresh(_ key: String) {
        endpointFreshUntil[key] = Date().addingTimeInterval(refreshThrottleInterval)
    }

    func createConversation(persist: Bool = true) {
        let now = Date().timeIntervalSince1970
        let session = DesktopSessionItem(
            id: "session_ios_\(UUID().uuidString.lowercased())",
            title: "新对话",
            status: "active",
            createdAt: now,
            updatedAt: now,
            messages: []
        )
        conversationSessions.append(session)
        activeConversationSessionID = session.id
        conversationMessages = []
        if persist {
            Task { await persistConversations() }
        }
    }

    func selectConversation(_ sessionID: String) async {
        await persistConversations()
        activeConversationSessionID = sessionID
        applyActiveConversation()
        await persistConversations()
    }

    func archiveConversation(_ sessionID: String) async {
        guard let index = conversationSessions.firstIndex(where: { $0.id == sessionID }) else { return }
        conversationSessions[index].status = "archived"
        conversationSessions[index].updatedAt = Date().timeIntervalSince1970
        await persistConversations()
    }

    func deleteConversation(_ sessionID: String) async {
        let linkedJobIDs = conversationJobSessions.compactMap { jobID, linkedSessionID in
            linkedSessionID == sessionID ? jobID : nil
        }
        for jobID in linkedJobIDs {
            conversationJobTasks[jobID]?.cancel()
            conversationJobTasks.removeValue(forKey: jobID)
            conversationJobSessions.removeValue(forKey: jobID)
        }
        conversationSessions.removeAll { $0.id == sessionID }
        if conversationSessions.isEmpty {
            createConversation(persist: false)
        }
        if activeConversationSessionID == sessionID {
            activeConversationSessionID = conversationSessions[0].id
            applyActiveConversation()
        }
        await persistConversations(deletedSessionIDs: [sessionID])
    }

    private func continueSpiritJob(_ jobID: String, sessionID: String, placeholderID: String) {
        guard !jobID.isEmpty, conversationJobTasks[jobID] == nil else { return }
        conversationJobSessions[jobID] = sessionID
        conversationJobTasks[jobID] = Task { [weak self] in
            guard let self else { return }
            defer {
                self.conversationJobTasks.removeValue(forKey: jobID)
                self.conversationJobSessions.removeValue(forKey: jobID)
            }
            guard let api = self.api else { return }
            var consecutiveErrors = 0
            for _ in 0..<180 {
                if Task.isCancelled { return }
                do {
                    try await Task.sleep(nanoseconds: 800_000_000)
                    switch try await api.spiritJobStatus(jobID) {
                    case .pending:
                        consecutiveErrors = 0
                        continue
                    case .completed(let reply):
                        self.replaceConversationMessage(
                            placeholderID,
                            with: .assistant,
                            text: reply,
                            sessionID: sessionID
                        )
                        self.statusMessage = "桌面 Runtime 回复已同步"
                        await self.persistConversations()
                        return
                    case .failed(let error):
                        self.replaceConversationMessage(
                            placeholderID,
                            with: .system,
                            text: "桌面 Runtime 任务失败：\(error)",
                            sessionID: sessionID
                        )
                        self.statusMessage = "桌面 Runtime 任务失败"
                        await self.persistConversations()
                        return
                    }
                } catch is CancellationError {
                    return
                } catch {
                    consecutiveErrors += 1
                    if consecutiveErrors < 3 { continue }
                    self.replaceConversationMessage(
                        placeholderID,
                        with: .system,
                        text: "后台同步失败：\(error.localizedDescription)。可在运行监控中重试或查看任务。",
                        sessionID: sessionID
                    )
                    self.statusMessage = "后台回复同步失败"
                    await self.persistConversations()
                    return
                }
            }
            self.replaceConversationMessage(
                placeholderID,
                with: .system,
                text: "任务仍在桌面 Runtime 队列中，可在运行监控中继续查看。",
                sessionID: sessionID
            )
            self.statusMessage = "桌面 Runtime 任务仍在运行"
            await self.persistConversations()
        }
    }

    private func appendConversationMessage(
        _ role: TerminalConversationMessage.Role,
        text: String,
        sessionID: String,
        messageID: String = UUID().uuidString
    ) {
        let message = TerminalConversationMessage(id: messageID, role: role, text: text)
        if activeConversationSessionID == sessionID {
            conversationMessages.append(message)
            return
        }
        guard let index = conversationSessions.firstIndex(where: { $0.id == sessionID }) else { return }
        conversationSessions[index].messages.append(DesktopSessionMessage(message))
        conversationSessions[index].updatedAt = Date().timeIntervalSince1970
    }

    private func replaceConversationMessage(
        _ messageID: String,
        with role: TerminalConversationMessage.Role,
        text: String,
        sessionID: String
    ) {
        let replacement = TerminalConversationMessage(id: messageID, role: role, text: text)
        if activeConversationSessionID == sessionID,
           let index = conversationMessages.firstIndex(where: { $0.id == messageID }) {
            conversationMessages[index] = replacement
            return
        }
        guard let sessionIndex = conversationSessions.firstIndex(where: { $0.id == sessionID }),
              let messageIndex = conversationSessions[sessionIndex].messages.firstIndex(where: { $0.id == messageID }) else { return }
        conversationSessions[sessionIndex].messages[messageIndex] = DesktopSessionMessage(replacement)
        conversationSessions[sessionIndex].updatedAt = Date().timeIntervalSince1970
    }

    private func applyActiveConversation() {
        guard let session = conversationSessions.first(where: { $0.id == activeConversationSessionID }) else {
            if conversationSessions.isEmpty {
                createConversation(persist: false)
            } else {
                activeConversationSessionID = conversationSessions[0].id
                applyActiveConversation()
            }
            return
        }
        conversationMessages = session.messages.map(TerminalConversationMessage.init)
    }

    private func persistConversations(deletedSessionIDs: [String] = []) async {
        guard let api else { return }
        let now = Date().timeIntervalSince1970
        if let index = conversationSessions.firstIndex(where: { $0.id == activeConversationSessionID }) {
            conversationSessions[index].messages = conversationMessages.map(DesktopSessionMessage.init)
            conversationSessions[index].updatedAt = now
            if conversationSessions[index].title == "新对话",
               let firstUserMessage = conversationMessages.first(where: { $0.role == .user }) {
                conversationSessions[index].title = String(firstUserMessage.text.prefix(32))
            }
        }
        let state = DesktopSessionState(
            revision: conversationRevision,
            activeSessionID: activeConversationSessionID,
            sessions: conversationSessions
        )
        do {
            let saved = try await api.updateSessions(state, deletedSessionIDs: deletedSessionIDs)
            conversationRevision = saved.revision
            // Keep local state authoritative for mutations made while the
            // request was in flight. The next explicit refresh merges desktop
            // sessions without dropping a queued placeholder or reply.
            markEndpointFresh("sessions")
        } catch {
            statusMessage = "会话保存失败：\(error.localizedDescription)"
        }
    }

    func uploadPhotos(_ items: [PhotosPickerItem], purpose: String) async {
        guard let api else {
            statusMessage = "Invalid Base URL"
            return
        }
        isLoading = true
        defer { isLoading = false }
        do {
            guard items.count <= 8 else { statusMessage = "一次最多选择 8 张图片"; return }
            var files: [DynamicJSON] = []
            var totalBytes = 0
            for item in items {
                if let data = try await item.loadTransferable(type: Data.self) {
                    totalBytes += data.count
                    guard data.count <= 8 * 1024 * 1024, totalBytes <= 12 * 1024 * 1024 else {
                        statusMessage = "图片总大小超过 12 MB，请减少选择或压缩图片"
                        return
                    }
                    let ext = preferredExtension(for: item.supportedContentTypes.first?.preferredMIMEType)
                    files.append(.object([
                        "name": .string("ios-upload-\(Int(Date().timeIntervalSince1970)).\(ext)"),
                        "mime_type": .string(item.supportedContentTypes.first?.preferredMIMEType ?? "application/octet-stream"),
                        "base64": .string(data.base64EncodedString())
                    ]))
                }
            }
            guard !files.isEmpty else {
                statusMessage = "No files selected"
                return
            }
            let response = try await api.uploadArtifact(payload: [
                "source": .string("ios_native_terminal"),
                "purpose": .string(purpose.isEmpty ? "ios_work_image" : purpose),
                "tags": .array([.string("ios"), .string("native")]),
                "files": .array(files)
            ])
            statusMessage = response.message ?? "Uploaded \(response.artifacts?.count ?? 0) artifact(s)"
            await refreshEcommerce(force: true)
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    @discardableResult
    func uploadDocuments(_ urls: [URL], purpose: String) async -> Bool {
        guard let api else {
            statusMessage = "Invalid Base URL"
            return false
        }
        isLoading = true
        defer { isLoading = false }
        do {
            guard urls.count <= 8 else { statusMessage = "一次最多选择 8 个文件"; return false }
            var files: [DynamicJSON] = []
            var totalBytes = 0
            for url in urls {
                let scoped = url.startAccessingSecurityScopedResource()
                defer { if scoped { url.stopAccessingSecurityScopedResource() } }
                let values = try url.resourceValues(forKeys: [.fileSizeKey, .contentTypeKey, .nameKey])
                let size = values.fileSize ?? 0
                totalBytes += size
                guard size <= 8 * 1024 * 1024, totalBytes <= 12 * 1024 * 1024 else {
                    statusMessage = "文件总大小超过 12 MB，请减少选择"
                    return false
                }
                let data = try await Task.detached(priority: .userInitiated) {
                    try Data(contentsOf: url, options: [.mappedIfSafe])
                }.value
                totalBytes += max(0, data.count - size)
                guard data.count <= 8 * 1024 * 1024, totalBytes <= 12 * 1024 * 1024 else {
                    statusMessage = "文件总大小超过 12 MB，请减少选择"
                    return false
                }
                files.append(.object([
                    "name": .string(values.name ?? url.lastPathComponent),
                    "mime_type": .string(values.contentType?.preferredMIMEType ?? "application/octet-stream"),
                    "base64": .string(data.base64EncodedString())
                ]))
            }
            guard !files.isEmpty else { statusMessage = "未选择文件"; return false }
            let response = try await api.uploadArtifact(payload: [
                "source": .string("ios_native_terminal"),
                "purpose": .string(purpose.isEmpty ? "ios_work_file" : purpose),
                "tags": .array([.string("ios"), .string("native"), .string("file")]),
                "files": .array(files)
            ])
            statusMessage = response.message ?? "文件已上传"
            await refreshEcommerce(force: true)
            return true
        } catch {
            statusMessage = "文件上传失败：\(error.localizedDescription)"
            return false
        }
    }

    func importSharedArtifacts() async {
        let pending = SharedArtifactInbox.pendingFiles()
        guard !pending.isEmpty else { return }
        if await uploadDocuments(pending, purpose: "ios_share_extension") {
            SharedArtifactInbox.remove(pending)
            statusMessage = "已导入 \(pending.count) 个分享文件"
        }
    }

    func diagnoseConnection() async {
        guard let url = URL(string: baseURLString.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            statusMessage = "Invalid Base URL"
            return
        }
        let healthURL = url.appendingPathComponent("ios/health")
        do {
            let (data, response) = try await URLSession.shared.data(from: healthURL)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            let body = String(data: data, encoding: .utf8) ?? ""
            statusMessage = "Health \(status): \(body.prefix(120))"
        } catch {
            statusMessage = "Connection failed: \(error.localizedDescription)"
        }
    }

    func resumeSafety(confirmation: String = "") async {
        await sendAction([
            "action": .string("resume"),
            "reason": .string("iOS native terminal resume"),
            "confirmation": .string(confirmation)
        ], successMessage: "Safety resumed")
    }

    func hardStop() async {
        await sendAction([
            "action": .string("hard_stop"),
            "reason": .string("iOS native terminal hard stop")
        ], successMessage: "Hard stop requested")
    }

    var metrics: [MetricItem] {
        let services = snapshot.services["services"].arrayValue
        let running = services.filter { $0["running"].boolValue }.count
        let modules = snapshot.moduleManagement
        let companion = androidCompanion
        let workflows = snapshot.workflows["overview"]
        let safety = snapshot.safety
        let modelGovernance = snapshot.modelGovernance
        return [
            MetricItem(title: "Services", value: "\(running)/\(services.count)", detail: "running"),
            MetricItem(title: "Modules", value: "\(modules["ready_count"].intValue)/\(modules["module_count"].intValue)", detail: "ready"),
            MetricItem(title: "Models", value: "\(modelGovernance["role_count"].intValue)", detail: "adapters \(modelGovernance["adapter_count"].intValue)"),
            MetricItem(title: "Android", value: "\(companion["pending_command_count"].intValue)", detail: "queued"),
            MetricItem(title: "Workflows", value: "\(workflows["available_definition_count"].intValue)", detail: "runs \(workflows["run_count"].intValue)"),
            MetricItem(title: "Safety", value: safety["active"].boolValue ? "STOP" : "normal", detail: safety["mode"].stringValue)
        ]
    }

    var workflowDefinitions: [WorkflowDefinitionItem] {
        let builtin = snapshot.workflows["builtin_definitions"].arrayValue
        let saved = snapshot.workflows["definitions"].arrayValue.isEmpty
            ? snapshot.workflows["definitions"]["definitions"].arrayValue
            : snapshot.workflows["definitions"].arrayValue
        var seen = Set<String>()
        return (builtin + saved).compactMap { item in
            let name = item["name"].stringValue
            guard !name.isEmpty, !seen.contains(name) else { return nil }
            seen.insert(name)
            let display = item["metadata"]["display_name"].stringValue
            let explicitDomain = item["domain"].stringValue.isEmpty
                ? item["metadata"]["domain"].stringValue
                : item["domain"].stringValue
            return WorkflowDefinitionItem(
                name: name,
                displayName: display.isEmpty ? name : display,
                domain: WorkflowDomain.classify(name, explicit: explicitDomain)
            )
        }
    }

    var workflowRuns: [WorkflowRunItem] {
        snapshot.workflows["runs"].arrayValue.map {
            WorkflowRunItem(
                runId: $0["run_id"].stringValue,
                workflowName: $0["workflow_name"].stringValue,
                status: $0["status"].stringValue,
                updatedAt: $0["updated_at"].stringValue.isEmpty ? $0["created_at"].stringValue : $0["updated_at"].stringValue,
                raw: $0
            )
        }
    }

    var androidCompanion: DynamicJSON {
        snapshot.mobileManagement["android"]["companion"]
    }

    var androidDevices: [AndroidDeviceItem] {
        androidCompanion["devices"].arrayValue.map {
            AndroidDeviceItem(
                deviceId: $0["device_id"].stringValue,
                online: $0["online"].boolValue,
                battery: $0["battery_pct"].stringValue.isEmpty ? "--" : $0["battery_pct"].stringValue,
                currentApp: $0["current_app"].stringValue.isEmpty ? "--" : $0["current_app"].stringValue,
                pending: $0["pending_command_count"].intValue,
                inflight: $0["inflight_command_count"].intValue
            )
        }
    }

    var recentCommands: [AndroidCommandItem] {
        androidCompanion["recent_commands"].arrayValue.reversed().prefix(12).map {
            AndroidCommandItem(
                commandId: $0["command_id"].stringValue,
                deviceId: $0["device_id"].stringValue,
                operation: $0["operation"].stringValue,
                status: $0["status"].stringValue,
                message: $0["message"].stringValue
            )
        }
    }

    var workspaceDeviceGroups: [WorkspaceDeviceGroup] {
        snapshot.mobileManagement["workspace_devices"]["items"].arrayValue.map { item in
            let counts = item["counts"]
            return WorkspaceDeviceGroup(
                workspaceID: item["workspace_id"].stringValue,
                name: item["name"].stringValue,
                status: item["status"].stringValue,
                androidCount: counts["android"].intValue,
                iosControllerCount: counts["ios_controllers"].intValue,
                remoteWorkerCount: counts["remote_workers"].intValue,
                activeBindingCount: counts["active_bindings"].intValue,
                pendingPairingCount: counts["pending_pairings"].intValue,
                lastSeenAt: item["last_seen_at"].stringValue,
                androidDevices: workspaceEntries(item["android_devices"].arrayValue),
                iosControllers: workspaceEntries(item["ios_controllers"].arrayValue),
                remoteWorkers: workspaceEntries(item["remote_workers"].arrayValue)
            )
        }
    }

    var recentArtifacts: [MobileArtifactItem] {
        ecommerceSnapshot["artifacts"]["recent"].arrayValue.flatMap { artifact in
            artifact["files"].arrayValue.enumerated().map { fileIndex, file in
                MobileArtifactItem(
                    artifactID: artifact["artifact_id"].stringValue,
                    fileIndex: fileIndex,
                    name: file["name"].stringValue,
                    mimeType: file["mime_type"].stringValue,
                    purpose: artifact["purpose"].stringValue,
                    source: artifact["source"].stringValue,
                    sizeBytes: file["size_bytes"].intValue,
                    createdAt: artifact["created_at"].stringValue,
                    raw: artifact
                )
            }
        }
    }

    func artifactURL(_ artifact: MobileArtifactItem) -> URL? {
        guard !artifact.artifactID.isEmpty else { return nil }
        return api?.artifactURL(artifactID: artifact.artifactID, fileIndex: artifact.fileIndex)
    }

    func artifactData(_ artifact: MobileArtifactItem) async throws -> Data {
        guard let api else { throw TerminalAPIError.invalidBaseURL }
        return try await api.artifactData(artifactID: artifact.artifactID, fileIndex: artifact.fileIndex)
    }

    private func preferredExtension(for mimeType: String?) -> String {
        switch mimeType {
        case "image/png":
            return "png"
        case "image/heic", "image/heif":
            return "heic"
        case "image/jpeg":
            return "jpg"
        default:
            return "bin"
        }
    }

    private func workspaceEntries(_ values: [DynamicJSON]) -> [WorkspaceDeviceEntry] {
        values.map {
            WorkspaceDeviceEntry(
                deviceID: $0["device_id"].stringValue.isEmpty ? "device" : $0["device_id"].stringValue,
                role: $0["role"].stringValue,
                roleLabel: $0["role_label"].stringValue,
                status: $0["status"].stringValue,
                lastSeenAt: $0["last_seen_at"].stringValue,
                foregroundPackage: $0["foreground_package"].stringValue
            )
        }
    }
}

struct TerminalConversationMessage: Identifiable {
    enum Role: Equatable {
        case assistant
        case user
        case system
    }

    let id: String
    let role: Role
    let text: String
    let createdAt: Double

    init(id: String = UUID().uuidString, role: Role, text: String, createdAt: Double = Date().timeIntervalSince1970) {
        self.id = id
        self.role = role
        self.text = text
        self.createdAt = createdAt
    }

    init(_ message: DesktopSessionMessage) {
        id = message.id
        role = message.role == "user" ? .user : (message.role == "system" ? .system : .assistant)
        text = message.text
        createdAt = message.createdAt
    }
}

private extension DesktopSessionMessage {
    init(_ message: TerminalConversationMessage) {
        id = message.id
        role = message.role == .user ? "user" : (message.role == .system ? "system" : "assistant")
        text = message.text
        createdAt = message.createdAt
        updatedAt = Date().timeIntervalSince1970
    }
}
