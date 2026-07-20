import Foundation

enum JSONHelpers {
    static func parseObject(_ text: String) -> [String: DynamicJSON] throws {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return [:] }
        let data = Data(trimmed.utf8)
        let decoded = try JSONDecoder().decode(DynamicJSON.self, from: data)
        return decoded.objectValue
    }

    static func pretty(_ value: DynamicJSON) -> String {
        guard
            let data = try? JSONEncoder().encode(value),
            let object = try? JSONSerialization.jsonObject(with: data),
            let pretty = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys]),
            let text = String(data: pretty, encoding: .utf8)
        else {
            return value.stringValue
        }
        return text
    }
}
