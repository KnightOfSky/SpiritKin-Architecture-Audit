import Foundation

enum SharedArtifactInbox {
    static let appGroupID = "group.com.spiritkin.shared"
    private static let directoryName = "SharedArtifacts"

    static func directory() throws -> URL {
        guard let container = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: appGroupID) else {
            throw SharedArtifactInboxError.appGroupUnavailable
        }
        let directory = container.appendingPathComponent(directoryName, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory
    }

    static func pendingFiles() -> [URL] {
        guard let directory = try? directory() else { return [] }
        let keys: Set<URLResourceKey> = [.isRegularFileKey, .contentModificationDateKey]
        let files = (try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: Array(keys))) ?? []
        return files.filter { (try? $0.resourceValues(forKeys: keys).isRegularFile) == true }
            .sorted {
                let left = (try? $0.resourceValues(forKeys: keys).contentModificationDate) ?? .distantPast
                let right = (try? $1.resourceValues(forKeys: keys).contentModificationDate) ?? .distantPast
                return left < right
            }
    }

    static func store(data: Data, suggestedName: String) throws -> URL {
        let safeName = sanitize(suggestedName)
        let target = try directory().appendingPathComponent("\(UUID().uuidString.lowercased())-\(safeName)")
        try data.write(to: target, options: .atomic)
        return target
    }

    static func remove(_ urls: [URL]) {
        urls.forEach { try? FileManager.default.removeItem(at: $0) }
    }

    private static func sanitize(_ value: String) -> String {
        let fallback = "shared-file.bin"
        let candidate = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !candidate.isEmpty else { return fallback }
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_."))
        let safe = candidate.unicodeScalars.map { allowed.contains($0) ? String($0) : "_" }
            .joined()
            .replacingOccurrences(of: "..", with: "_")
        return safe.isEmpty ? fallback : String(safe.suffix(180))
    }
}

enum SharedArtifactInboxError: LocalizedError {
    case appGroupUnavailable

    var errorDescription: String? {
        "SpiritKin App Group is unavailable. Enable \(SharedArtifactInbox.appGroupID) for the app and Share Extension."
    }
}
