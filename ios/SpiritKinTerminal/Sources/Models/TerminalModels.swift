import Foundation

struct TerminalSnapshot: Codable, Equatable {
    var ok: Bool
    var services: DynamicJSON
    var servicePorts: DynamicJSON
    var safety: DynamicJSON
    var mobileManagement: DynamicJSON
    var workflows: DynamicJSON
    var moduleManagement: DynamicJSON
    var modelGovernance: DynamicJSON
    var snapshotMeta: DynamicJSON

    enum CodingKeys: String, CodingKey {
        case ok
        case services
        case servicePorts = "service_ports"
        case safety
        case mobileManagement = "mobile_management"
        case workflows
        case moduleManagement = "module_management"
        case modelGovernance = "model_governance"
        case snapshotMeta = "snapshot_meta"
    }

    init(
        ok: Bool,
        services: DynamicJSON,
        servicePorts: DynamicJSON,
        safety: DynamicJSON,
        mobileManagement: DynamicJSON,
        workflows: DynamicJSON,
        moduleManagement: DynamicJSON,
        modelGovernance: DynamicJSON,
        snapshotMeta: DynamicJSON
    ) {
        self.ok = ok
        self.services = services
        self.servicePorts = servicePorts
        self.safety = safety
        self.mobileManagement = mobileManagement
        self.workflows = workflows
        self.moduleManagement = moduleManagement
        self.modelGovernance = modelGovernance
        self.snapshotMeta = snapshotMeta
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ok = try container.decodeIfPresent(Bool.self, forKey: .ok) ?? false
        services = try container.decodeIfPresent(DynamicJSON.self, forKey: .services) ?? .object([:])
        servicePorts = try container.decodeIfPresent(DynamicJSON.self, forKey: .servicePorts) ?? .object([:])
        safety = try container.decodeIfPresent(DynamicJSON.self, forKey: .safety) ?? .object([:])
        mobileManagement = try container.decodeIfPresent(DynamicJSON.self, forKey: .mobileManagement) ?? .object([:])
        workflows = try container.decodeIfPresent(DynamicJSON.self, forKey: .workflows) ?? .object([:])
        moduleManagement = try container.decodeIfPresent(DynamicJSON.self, forKey: .moduleManagement) ?? .object([:])
        modelGovernance = try container.decodeIfPresent(DynamicJSON.self, forKey: .modelGovernance) ?? .object([:])
        snapshotMeta = try container.decodeIfPresent(DynamicJSON.self, forKey: .snapshotMeta) ?? .object([:])
    }

    static let empty = TerminalSnapshot(
        ok: false,
        services: .object([:]),
        servicePorts: .object([:]),
        safety: .object([:]),
        mobileManagement: .object([:]),
        workflows: .object([:]),
        moduleManagement: .object([:]),
        modelGovernance: .object([:]),
        snapshotMeta: .object([:])
    )
}

struct TerminalActionResponse: Codable {
    var ok: Bool
    var iosControl: TerminalSnapshot?
    var message: String?
    var error: String?
    var detail: String?

    enum CodingKeys: String, CodingKey {
        case ok
        case iosControl = "ios_control"
        case message
        case error
        case detail
    }
}

struct IOSCapabilityItem: Codable, Identifiable {
    let capabilityID: String
    let label: String
    let detail: String
    var enabled: Bool
    let locked: Bool

    var id: String { capabilityID }

    enum CodingKeys: String, CodingKey {
        case capabilityID = "capability_id"
        case label
        case detail
        case enabled
        case locked
    }
}

struct IOSCapabilitySnapshot: Codable {
    var capabilities: [IOSCapabilityItem]
    var enabledCount: Int
    var capabilityCount: Int

    enum CodingKeys: String, CodingKey {
        case capabilities
        case enabledCount = "enabled_count"
        case capabilityCount = "capability_count"
    }
}

struct IOSPoolSnapshot: Codable {
    var skills: DynamicJSON
    var workflows: DynamicJSON
}

struct ArtifactUploadResponse: Codable {
    var ok: Bool
    var message: String?
    var error: String?
    var artifacts: [DynamicJSON]?
}

struct MetricItem: Identifiable {
    var id: String { title }
    let title: String
    let value: String
    let detail: String
}

struct WorkflowDefinitionItem: Identifiable, Equatable {
    var id: String { name }
    let name: String
    let displayName: String
    let domain: WorkflowDomain
}

enum WorkflowDomain: String, CaseIterable, Identifiable {
    case ecommerce
    case content
    case engineering
    case system
    case general

    var id: String { rawValue }

    var title: String {
        switch self {
        case .ecommerce: return "电商"
        case .content: return "内容与媒体"
        case .engineering: return "开发与自动化"
        case .system: return "系统与治理"
        case .general: return "其他"
        }
    }

    var subtitle: String {
        switch self {
        case .ecommerce: return "选品、素材、发布预检、Android 上架与 Terminal"
        case .content: return "视频、图片、语音和 AI Cover"
        case .engineering: return "代码、浏览器、脚本、测试与远程执行"
        case .system: return "运行时、模型、诊断、安全与状态维护"
        case .general: return "尚未归入专门领域的工作流"
        }
    }

    var systemImage: String {
        switch self {
        case .ecommerce: return "bag"
        case .content: return "play.rectangle"
        case .engineering: return "hammer"
        case .system: return "gearshape"
        case .general: return "square.grid.2x2"
        }
    }

    static func classify(_ name: String, explicit: String = "") -> WorkflowDomain {
        if let domain = WorkflowDomain(rawValue: explicit.lowercased()), !explicit.isEmpty {
            return domain
        }
        let value = name.lowercased()
        let mappings: [(WorkflowDomain, [String])] = [
            (.ecommerce, ["ecommerce", "commerce", "listing", "product", "pdd", "taobao", "jd"]),
            (.content, ["content", "video", "image", "audio", "voice", "cover", "music"]),
            (.engineering, ["code", "dev", "git", "browser", "cli", "test", "automation", "game"]),
            (.system, ["runtime", "health", "diagnostic", "service", "model", "safety", "maintenance"])
        ]
        return mappings.first(where: { pair in
            pair.1.contains(where: { keyword in value.contains(keyword) })
        })?.0 ?? .general
    }
}

struct DesktopSessionState: Codable {
    var ok: Bool = true
    var revision: Int
    var activeSessionID: String
    var sessions: [DesktopSessionItem]

    enum CodingKeys: String, CodingKey {
        case ok
        case revision
        case activeSessionID = "active_session_id"
        case sessions
    }
}

struct DesktopSessionItem: Codable, Identifiable, Equatable {
    var id: String
    var title: String
    var status: String
    var createdAt: Double
    var updatedAt: Double
    var messages: [DesktopSessionMessage]

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case status
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case messages
    }
}

struct DesktopSessionMessage: Codable, Identifiable, Equatable {
    var id: String
    var role: String
    var text: String
    var createdAt: Double
    var updatedAt: Double

    enum CodingKeys: String, CodingKey {
        case id
        case role
        case text
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

struct WorkflowRunItem: Identifiable {
    var id: String { runId }
    let runId: String
    let workflowName: String
    let status: String
    let updatedAt: String
    let raw: DynamicJSON
}

struct AndroidDeviceItem: Identifiable {
    var id: String { deviceId }
    let deviceId: String
    let online: Bool
    let battery: String
    let currentApp: String
    let pending: Int
    let inflight: Int
}

struct AndroidCommandItem: Identifiable {
    var id: String { commandId.isEmpty ? "\(deviceId)-\(operation)-\(status)-\(message)" : commandId }
    let commandId: String
    let deviceId: String
    let operation: String
    let status: String
    let message: String
}

struct WorkspaceDeviceGroup: Identifiable {
    var id: String { workspaceID }
    let workspaceID: String
    let name: String
    let status: String
    let androidCount: Int
    let iosControllerCount: Int
    let remoteWorkerCount: Int
    let activeBindingCount: Int
    let pendingPairingCount: Int
    let lastSeenAt: String
    let androidDevices: [WorkspaceDeviceEntry]
    let iosControllers: [WorkspaceDeviceEntry]
    let remoteWorkers: [WorkspaceDeviceEntry]
}

struct WorkspaceDeviceEntry: Identifiable {
    var id: String { "\(role)-\(deviceID)" }
    let deviceID: String
    let role: String
    let roleLabel: String
    let status: String
    let lastSeenAt: String
    let foregroundPackage: String
}

struct MobileArtifactItem: Identifiable {
    var id: String { artifactID.isEmpty ? "\(name)-\(source)-\(createdAt)-\(fileIndex)" : "\(artifactID)-\(fileIndex)" }
    let artifactID: String
    let fileIndex: Int
    let name: String
    let mimeType: String
    let purpose: String
    let source: String
    let sizeBytes: Int
    let createdAt: String
    let raw: DynamicJSON

    var isImage: Bool {
        mimeType.lowercased().hasPrefix("image/")
    }

    var isAudio: Bool {
        mimeType.lowercased().hasPrefix("audio/")
    }
}
