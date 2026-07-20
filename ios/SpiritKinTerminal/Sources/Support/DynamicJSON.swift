import Foundation

enum DynamicJSON: Codable, Equatable {
    case object([String: DynamicJSON])
    case array([DynamicJSON])
    case string(String)
    case number(Double)
    case bool(Bool)
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([String: DynamicJSON].self) {
            self = .object(value)
        } else {
            self = .array(try container.decode([DynamicJSON].self))
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .object(let value):
            try container.encode(value)
        case .array(let value):
            try container.encode(value)
        case .string(let value):
            try container.encode(value)
        case .number(let value):
            try container.encode(value)
        case .bool(let value):
            try container.encode(value)
        case .null:
            try container.encodeNil()
        }
    }

    var objectValue: [String: DynamicJSON] {
        if case .object(let value) = self { return value }
        return [:]
    }

    var arrayValue: [DynamicJSON] {
        if case .array(let value) = self { return value }
        return []
    }

    var stringValue: String {
        switch self {
        case .string(let value):
            return value
        case .number(let value):
            return value == floor(value) ? String(Int(value)) : String(value)
        case .bool(let value):
            return value ? "true" : "false"
        default:
            return ""
        }
    }

    var boolValue: Bool {
        switch self {
        case .bool(let value):
            return value
        case .string(let value):
            return ["1", "true", "yes", "on", "running", "ready"].contains(value.lowercased())
        case .number(let value):
            return value != 0
        default:
            return false
        }
    }

    var intValue: Int {
        switch self {
        case .number(let value):
            return Int(value)
        case .string(let value):
            return Int(value) ?? 0
        case .bool(let value):
            return value ? 1 : 0
        default:
            return 0
        }
    }

    var doubleValue: Double {
        switch self {
        case .number(let value):
            return value
        case .string(let value):
            return Double(value) ?? 0
        case .bool(let value):
            return value ? 1 : 0
        default:
            return 0
        }
    }

    subscript(_ key: String) -> DynamicJSON {
        objectValue[key] ?? .null
    }
}

extension DynamicJSON {
    static func fromAny(_ value: Any?) -> DynamicJSON {
        guard let value else { return .null }
        if let value = value as? DynamicJSON { return value }
        if let value = value as? String { return .string(value) }
        if let value = value as? Bool { return .bool(value) }
        if let value = value as? Int { return .number(Double(value)) }
        if let value = value as? Double { return .number(value) }
        if let value = value as? [String: Any] {
            return .object(value.mapValues { DynamicJSON.fromAny($0) })
        }
        if let value = value as? [Any] {
            return .array(value.map { DynamicJSON.fromAny($0) })
        }
        return .string(String(describing: value))
    }
}
