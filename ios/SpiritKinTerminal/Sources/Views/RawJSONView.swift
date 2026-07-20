import SwiftUI

struct RawJSONView: View {
    let title: String
    let value: DynamicJSON

    var body: some View {
        ScrollView([.vertical, .horizontal]) {
            Text(JSONHelpers.pretty(value))
                .font(.system(.caption, design: .monospaced))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding()
        }
        .navigationTitle(title)
    }
}
