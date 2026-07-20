import Foundation

enum SemanticIcons {
    static let names: [String: String] = [
        "action.add": "plus", "action.close": "xmark", "action.more": "ellipsis",
        "action.refresh": "arrow.clockwise", "action.search": "magnifyingglass",
        "action.send": "paperplane", "action.attach": "paperclip", "action.copy": "doc.on.doc",
        "action.edit": "pencil", "action.delete": "trash", "action.play": "play.fill",
        "action.stop": "stop.fill", "action.resume": "arrow.counterclockwise",
        "action.settings": "gearshape", "action.terminal": "apple.terminal",
        "navigation.expand": "chevron.down", "navigation.collapse": "chevron.up",
        "navigation.back": "chevron.backward", "navigation.forward": "chevron.forward",
        "entity.chat": "message", "entity.project": "folder",
        "entity.workflow": "point.3.connected.trianglepath.dotted", "entity.mobile": "iphone",
        "state.info": "info.circle", "state.success": "checkmark.circle",
        "state.warning": "exclamationmark.triangle", "state.danger": "xmark.circle",
        "state.unknown": "questionmark.circle", "state.loading": "progress.indicator"
    ]

    static func systemName(_ semanticID: String) -> String {
        names[semanticID] ?? names["state.unknown"]!
    }
}
