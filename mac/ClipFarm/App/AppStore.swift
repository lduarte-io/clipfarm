import CFDomain
import CFExport
import CFMedia
import CFStore
import Foundation
import Observation

/// The one app-wide store (§2.7): views render its state and call its
/// methods — never a Kit module directly. N3 scope: library open, the
/// footage inbox, ingest (probe/remux seams wired to CFMedia/CFExport),
/// segmentation settings, per-source re-apply, post-ingest waveforms.
/// GRDB `ValueObservation`-fed read models arrive at N4; until then the
/// source list is refetched after each mutation (personal-library scale).
@MainActor
@Observable
final class AppStore {
    struct SourceRow: Identifiable {
        let id: String
        let source: Source
        let clipCount: Int
    }

    private(set) var library: LibraryStore?
    private(set) var openError: String?
    private(set) var sourceRows: [SourceRow] = []
    private(set) var librarySettings = LibrarySettings()
    private(set) var isIngesting = false
    private(set) var lastIngestResult: IngestResult?
    private(set) var lastActionError: String?
    /// Informational outcomes (e.g. a no-op re-apply) — never the error
    /// channel (cold-review finding 6).
    private(set) var lastActionInfo: String?

    private var waveformService: WaveformService?

    /// The footage inbox (D34): outside any cloud-synced path, created on
    /// first run; the ingest picker defaults here. A managed working folder
    /// — not canonical storage.
    static var footageInboxURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("ClipFarm/Footage", isDirectory: true)
    }

    /// Opens the default library (D28 `~/ClipFarm/`) with the window's
    /// UndoManager so the system Edit menu drives store undo directly.
    ///
    /// Safe to call again when the environment's undo manager materializes
    /// late (cold-review finding 4): an already-open store adopts it.
    func openDefaultLibraryIfNeeded(undoManager: UndoManager?) {
        if let library {
            if let undoManager { library.adoptUndoManager(undoManager) }
            return
        }
        print("clipfarm: opening library at \(LibraryStore.defaultLibraryFolderURL.path)")
        if undoManager == nil {
            // Store undo silently no-ops without one; leave a breadcrumb so
            // a dead Cmd+Z at the manual verify has a diagnosis.
            print("clipfarm: WARNING — opening with a nil UndoManager (environment not ready?)")
        }
        do {
            try FileManager.default.createDirectory(
                at: Self.footageInboxURL, withIntermediateDirectories: true)
            let store = try LibraryStore.open(
                at: LibraryStore.defaultLibraryFolderURL, undoManager: undoManager)
            library = store
            waveformService = WaveformService(
                cacheDirectory: store.folderURL
                    .appendingPathComponent("cache/waveforms", isDirectory: true))
            refresh()
        } catch {
            openError = String(describing: error)
            print("clipfarm: library open FAILED: \(error)")
        }
    }

    func refresh() {
        guard let library else { return }
        do {
            let state = try library.fetchState()
            var counts: [String: Int] = [:]
            for clip in state.clips.values {
                counts[clip.sourceID, default: 0] += 1
            }
            sourceRows = state.sources
                .map { SourceRow(id: $0.key, source: $0.value, clipCount: counts[$0.key] ?? 0) }
                .sorted { $0.source.filename < $1.source.filename }
            librarySettings = try library.librarySettings()
        } catch {
            lastActionError = String(describing: error)
        }
    }

    // MARK: - Ingest

    func ingest(folderURL: URL) async {
        guard let library, !isIngesting else { return }
        isIngesting = true
        lastActionError = nil
        lastActionInfo = nil
        defer { isIngesting = false }
        do {
            let result = try await library.ingestFolder(
                at: folderURL,
                probe: { url in try? await MetadataProbe.probe(url: url).probedSourceInfo },
                remux: { url in try await MKVRemuxer.remux(mkvURL: url) }
            )
            lastIngestResult = result
            refresh()
            generateMissingWaveforms()
        } catch {
            lastActionError = String(describing: error)
        }
    }

    /// Post-ingest background job (plan §4/N3): waveforms never block
    /// ingest; N11 degrades gracefully until a strip exists.
    private func generateMissingWaveforms() {
        guard let waveformService else { return }
        for row in sourceRows where !row.source.unavailable {
            let sourceURL = URL(fileURLWithPath: row.source.path)
            let sourceID = row.id
            Task {
                try? await waveformService.generateIfNeeded(
                    forSourceID: sourceID, sourceURL: sourceURL)
            }
        }
    }

    // MARK: - Segmentation settings (D18)

    func updateLibrarySettings(_ settings: LibrarySettings) {
        guard let library else { return }
        do {
            try library.updateLibrarySettings(settings)
            librarySettings = settings
        } catch {
            lastActionError = String(describing: error)
        }
    }

    func reapplySegmentation(sourceID: String) {
        guard let library else { return }
        do {
            let result = try library.reapplySegmentation(forSourceID: sourceID)
            lastActionInfo = result.changed
                ? "Re-apply: \(result.clipsAdded) added, \(result.clipsRemoved) removed, "
                    + "\(result.clipsKept) kept, \(result.skippedBoundaryEdited) hand-corrected skipped."
                : "Re-apply: no boundary changes under the current settings."
            refresh()
        } catch {
            lastActionError = String(describing: error)
        }
    }
}
