import SwiftUI

struct MetricCard: View {
    let metric: MetricItem

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(metric.title)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(metric.value)
                .font(.title3.weight(.semibold))
            Text(metric.detail)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(FantasyTheme.surface, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(FantasyTheme.line))
    }
}

struct SectionBlock<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title)
                .font(.headline)
                .foregroundStyle(FantasyTheme.primary)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(FantasyTheme.surface, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(FantasyTheme.line))
    }
}

struct RowLine: View {
    let title: String
    let subtitle: String
    let status: String

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Circle()
                .fill(statusColor)
                .frame(width: 9, height: 9)
                .padding(.top, 5)
            VStack(alignment: .leading, spacing: 3) {
                Text(title.isEmpty ? "--" : title)
                    .font(.body.weight(.medium))
                if !subtitle.isEmpty {
                    Text(subtitle)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
            Text(status.isEmpty ? "--" : status)
                .font(.caption.weight(.medium))
                .foregroundStyle(statusColor)
        }
        .padding(.vertical, 4)
    }

    private var statusColor: Color {
        FantasyTheme.statusColor(for: status)
    }
}

struct StatusBar: View {
    let text: String

    var body: some View {
        Text(text.isEmpty ? "Ready" : text)
            .font(.footnote)
            .foregroundStyle(FantasyTheme.muted)
            .lineLimit(2)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal)
            .padding(.vertical, 8)
            .background(.thinMaterial)
            .overlay(alignment: .top) {
                Rectangle().fill(FantasyTheme.line).frame(height: 1)
            }
    }
}

extension View {
    /// 把页面底色统一到 Fantasy canvas token（#F7FAFF），
    /// 并隐藏 Form/List 默认的系统分组灰底，使三端底色一致。
    func fantasyCanvas() -> some View {
        scrollContentBackground(.hidden)
            .background(FantasyTheme.canvas)
    }
}
