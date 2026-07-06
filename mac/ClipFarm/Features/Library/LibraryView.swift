import AppKit
import CFStore
import SwiftUI
import UniformTypeIdentifiers

/// Library page, N3 scope: ingest a folder (picker defaults to the footage
/// inbox; drag-a-folder-in works too), see sources with clip counts, run
/// the per-source "Re-apply Segmentation" action. The transcript browser,
/// search, and click-to-play arrive at N4.
struct LibraryView: View {
    @Environment(AppStore.self) private var appStore
    @State private var isDropTargeted = false

    var body: some View {
        Group {
            if let error = appStore.openError {
                ContentUnavailableView(
                    "Library failed to open",
                    systemImage: "exclamationmark.triangle",
                    description: Text(error)
                )
            } else if appStore.sourceRows.isEmpty {
                emptyState
            } else {
                sourceList
            }
        }
        .toolbar {
            ToolbarItem {
                Button {
                    presentIngestPanel()
                } label: {
                    Label("Ingest Folder…", systemImage: "square.and.arrow.down.on.square")
                }
                .disabled(appStore.isIngesting)
                .help("Ingest a folder of video files (defaults to the footage inbox)")
            }
        }
        .dropDestination(for: URL.self) { urls, _ in
            guard let folder = urls.first(where: Self.isDirectory) else { return false }
            Task { await appStore.ingest(folderURL: folder) }
            return true
        } isTargeted: { targeted in
            isDropTargeted = targeted
        }
        .overlay {
            if isDropTargeted {
                RoundedRectangle(cornerRadius: 12)
                    .strokeBorder(Color.accentColor, style: StrokeStyle(lineWidth: 3, dash: [8]))
                    .padding(6)
                    .allowsHitTesting(false)
            }
        }
        .overlay(alignment: .bottom) {
            statusBar
        }
    }

    private var emptyState: some View {
        ContentUnavailableView {
            Label("No sources yet", systemImage: "books.vertical")
        } description: {
            Text(
                """
                Drop a folder of video files here (.mov / .mp4 / .m4v / .mkv, \
                each ideally with its <name>.whisper.json transcript sidecar), \
                or click Ingest Folder…

                The footage inbox is \(AppStore.footageInboxURL.path)
                """
            )
        } actions: {
            Button("Ingest Folder…") { presentIngestPanel() }
                .disabled(appStore.isIngesting)
        }
    }

    private var sourceList: some View {
        List {
            Section("Sources") {
                ForEach(appStore.sourceRows) { row in
                    sourceRowView(row)
                }
            }
        }
    }

    private func sourceRowView(_ row: AppStore.SourceRow) -> some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text(row.source.filename)
                    .font(.body.weight(.medium))
                HStack(spacing: 8) {
                    Text(Self.durationLabel(row.source.durationSec))
                    if let fps = row.source.fps {
                        Text("\(fps, format: .number.precision(.fractionLength(0...2))) fps")
                    }
                    if row.source.isHDR == true { Text("HDR") }
                    if row.source.transcriptPath == nil {
                        Text("no transcript — footage only")
                            .foregroundStyle(.orange)
                    }
                    if row.source.originalPath != nil {
                        Text("remuxed from .mkv")
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            Spacer()
            Text("\(row.clipCount) clips")
                .font(.callout.monospacedDigit())
                .foregroundStyle(.secondary)
        }
        .opacity(row.source.unavailable ? 0.4 : 1.0)
        .contextMenu {
            Button("Re-apply Segmentation") {
                appStore.reapplySegmentation(sourceID: row.id)
            }
            .disabled(row.source.transcriptPath == nil)
        }
    }

    @ViewBuilder
    private var statusBar: some View {
        if appStore.isIngesting {
            statusCapsule { ProgressView().controlSize(.small); Text("Ingesting…") }
        } else if let error = appStore.lastActionError {
            statusCapsule { Text(error).foregroundStyle(.orange) }
        } else if let result = appStore.lastIngestResult {
            statusCapsule {
                Text(Self.summary(of: result))
                if !result.rejected.isEmpty {
                    Text(result.rejected.map { "\($0.filename): \($0.reason.rawValue)" }
                        .joined(separator: " · "))
                        .foregroundStyle(.orange)
                }
            }
        }
    }

    private func statusCapsule(@ViewBuilder _ content: () -> some View) -> some View {
        HStack(spacing: 8, content: content)
            .font(.callout)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(.regularMaterial, in: Capsule())
            .padding(.bottom, 10)
    }

    private func presentIngestPanel() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.directoryURL = AppStore.footageInboxURL
        panel.message = "Choose a folder of video files to ingest"
        panel.prompt = "Ingest"
        if panel.runModal() == .OK, let folder = panel.url {
            Task { await appStore.ingest(folderURL: folder) }
        }
    }

    private static func isDirectory(_ url: URL) -> Bool {
        var isDirectory: ObjCBool = false
        return FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory)
            && isDirectory.boolValue
    }

    private static func durationLabel(_ seconds: Double?) -> String {
        guard let seconds else { return "—" }
        let total = Int(seconds.rounded())
        return String(format: "%d:%02d", total / 60, total % 60)
    }

    private static func summary(of result: IngestResult) -> String {
        "Ingest: \(result.sourcesAdded.count) added, "
            + "\(result.sourcesUpdated.count) updated, "
            + "\(result.sourcesSkipped.count) skipped, "
            + "\(result.rejected.count) rejected — \(result.clipsDetected) clips detected"
    }
}
