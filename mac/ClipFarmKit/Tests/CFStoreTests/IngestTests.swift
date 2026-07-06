import CFDomain
import Foundation
import GRDB
import Testing
@testable import CFStore

/// Port of `tests/test_ingest.py` (11 tests) + the N3 native additions
/// (`.mkv` remux path, probe-field recording, settings-driven segmentation,
/// FTS-at-ingest, undo grouping).
///
/// Like the reference suite, video files are zero-byte placeholders and the
/// probe is stubbed (`probe_video` was patched there; `SourceProbe` is
/// injected here). Real-media probing is CFMedia's job, tested in
/// CFMediaTests.

// MARK: - Helpers

@discardableResult
func writeSidecar(
    in folder: URL,
    stem: String,
    schemaVersion: Int = 1,
    duration: Double? = 60.0,
    wordGroups: [[(start: Double, end: Double, word: String)]]? = nil
) throws -> URL {
    // Default: two segments separated by a 3-second gap -> two clips.
    let groups = wordGroups ?? [
        [(0.0, 0.5, " hello"), (0.6, 1.0, " world")],
        [(4.0, 4.5, " second"), (4.6, 5.0, " clip")],
    ]
    var segments: [[String: Any]] = []
    for (index, group) in groups.enumerated() where !group.isEmpty {
        segments.append([
            "id": index,
            "start": group.first!.start,
            "end": group.last!.end,
            "words": group.map {
                ["start": $0.start, "end": $0.end, "word": $0.word, "probability": 0.9]
            },
        ])
    }
    var payload: [String: Any] = [
        "schema_version": schemaVersion,
        "source_filename": "\(stem).mov",
        "segments": segments,
    ]
    if let duration { payload["duration"] = duration }
    let url = folder.appendingPathComponent("\(stem).whisper.json")
    try JSONSerialization.data(withJSONObject: payload).write(to: url)
    return url
}

@discardableResult
func touchVideo(in folder: URL, name: String) throws -> URL {
    let url = folder.appendingPathComponent(name)
    try Data().write(to: url)
    return url
}

/// The reference's `_stub_ffprobe(fps=60.0, duration=12.5)`.
let stubProbe: SourceProbe = { _ in ProbedSourceInfo(durationSec: 12.5, fps: 60.0) }
/// Probe failure — the ported "both fields None" fallback path.
let failingProbe: SourceProbe = { _ in nil }
/// For folders that contain no `.mkv`: remux must never be called.
let unusedRemux: MKVRemux = { url in
    Issue.record("remux unexpectedly called for \(url.lastPathComponent)")
    return url
}
/// Stub remux: writes a placeholder sibling `.mp4` (skip-if-exists).
let copyingRemux: MKVRemux = { mkv in
    let mp4 = mkv.deletingPathExtension().appendingPathExtension("mp4")
    if !FileManager.default.fileExists(atPath: mp4.path) {
        try Data("remuxed".utf8).write(to: mp4)
    }
    return mp4
}

/// Scratch store + a separate media folder for fake footage.
@MainActor
@discardableResult
func withIngestFixture<T>(
    undoManager: UndoManager? = nil,
    _ body: @MainActor (LibraryStore, URL) async throws -> T
) async throws -> T {
    let root = try makeScratchFolder()
    defer { try? FileManager.default.removeItem(at: root) }
    let media = root.appendingPathComponent("media")
    try FileManager.default.createDirectory(at: media, withIntermediateDirectories: true)
    let store = try LibraryStore.open(at: root.appendingPathComponent("library"), undoManager: undoManager)
    defer { try? store.close() }
    return try await body(store, media)
}

// MARK: - Ported ingest tests (11)

@MainActor @Test func happyPathTwoPairedSources() async throws {
    try await withIngestFixture { store, media in
        try touchVideo(in: media, name: "alpha.mov")
        try writeSidecar(in: media, stem: "alpha")
        try touchVideo(in: media, name: "beta.mov")
        try writeSidecar(in: media, stem: "beta")

        let result = try await store.ingestFolder(at: media, probe: stubProbe, remux: unusedRemux)

        #expect(result.sourcesAdded.sorted() == ["alpha.mov", "beta.mov"])
        #expect(result.sourcesSkipped.isEmpty)
        #expect(result.rejected.isEmpty)
        #expect(result.clipsDetected == 4)

        let state = try store.fetchState()
        #expect(state.sources.count == 2)
        #expect(state.clips.count == 4)
        let sourceIDs = Set(state.sources.keys)
        #expect(Set(state.clips.values.map(\.sourceID)).isSubset(of: sourceIDs))
        for source in state.sources.values {
            #expect(source.fps == 60.0)
            // Sidecar duration (60.0) wins over the probe (12.5).
            #expect(source.durationSec == 60.0)
        }
    }
}

@MainActor @Test func transcriptLessSourceIsFootageOnly() async throws {
    try await withIngestFixture { store, media in
        try touchVideo(in: media, name: "no_transcript.mov")

        let result = try await store.ingestFolder(at: media, probe: stubProbe, remux: unusedRemux)

        #expect(result.sourcesAdded == ["no_transcript.mov"])
        #expect(result.clipsDetected == 0)
        let state = try store.fetchState()
        #expect(state.sources.count == 1)
        let source = try #require(state.sources.values.first)
        #expect(source.transcriptPath == nil)
        #expect(result.warnings.joined(separator: " ").contains("no sidecar transcript"))
    }
}

@MainActor @Test func reservedSeparatorInFilenameRejected() async throws {
    try await withIngestFixture { store, media in
        try touchVideo(in: media, name: "good.mov")
        try writeSidecar(in: media, stem: "good")
        try touchVideo(in: media, name: "bad__file.mov")

        let result = try await store.ingestFolder(at: media, probe: stubProbe, remux: unusedRemux)

        #expect(result.sourcesAdded == ["good.mov"])
        let rejection = try #require(result.rejected.first)
        #expect(result.rejected.count == 1)
        #expect(rejection.filename == "bad__file.mov")
        #expect(rejection.reason == .filenameContainsSeparator)
        #expect(rejection.sanitizedRename == "bad_file.mov")
        // No partial-state damage.
        let filenames = try store.fetchState().sources.values.map(\.filename)
        #expect(!filenames.contains("bad__file.mov"))
    }
}

@MainActor @Test func schemaVersionMismatchRejectsButContinues() async throws {
    try await withIngestFixture { store, media in
        try touchVideo(in: media, name: "good.mov")
        try writeSidecar(in: media, stem: "good")
        try touchVideo(in: media, name: "from_the_future.mov")
        try writeSidecar(in: media, stem: "from_the_future", schemaVersion: 2)

        let result = try await store.ingestFolder(at: media, probe: stubProbe, remux: unusedRemux)

        #expect(result.sourcesAdded.contains("good.mov"))
        // Still registered, footage-only.
        #expect(result.sourcesAdded.contains("from_the_future.mov"))
        #expect(result.rejected.contains { $0.reason == .schemaVersionMismatch })
    }
}

@MainActor @Test func malformedTranscriptRejectedSourceStillAddedAsFootageOnly() async throws {
    try await withIngestFixture { store, media in
        try touchVideo(in: media, name: "broken.mov")
        try Data("not valid json{".utf8).write(to: media.appendingPathComponent("broken.whisper.json"))

        let result = try await store.ingestFolder(at: media, probe: stubProbe, remux: unusedRemux)

        #expect(result.rejected.contains { $0.reason == .transcriptMalformed })
        #expect(result.sourcesAdded.contains("broken.mov"))
        let state = try store.fetchState()
        let source = try #require(state.sources.values.first)
        #expect(source.transcriptPath == nil)
        #expect(state.clips.isEmpty)
    }
}

@MainActor @Test func reIngestIsIdempotent() async throws {
    try await withIngestFixture { store, media in
        try touchVideo(in: media, name: "alpha.mov")
        try writeSidecar(in: media, stem: "alpha")

        _ = try await store.ingestFolder(at: media, probe: stubProbe, remux: unusedRemux)
        let second = try await store.ingestFolder(at: media, probe: stubProbe, remux: unusedRemux)

        #expect(second.sourcesAdded.isEmpty)
        #expect(second.sourcesSkipped == ["alpha.mov"])
        #expect(second.clipsDetected == 0)
        let state = try store.fetchState()
        #expect(state.sources.count == 1)
        #expect(state.clips.count == 2)
    }
}

@MainActor @Test func transcriptAppearingLaterUpgradesSource() async throws {
    try await withIngestFixture { store, media in
        try touchVideo(in: media, name: "alpha.mov")
        let first = try await store.ingestFolder(at: media, probe: stubProbe, remux: unusedRemux)
        #expect(first.sourcesAdded == ["alpha.mov"])
        #expect(first.clipsDetected == 0)

        try writeSidecar(in: media, stem: "alpha")
        let second = try await store.ingestFolder(at: media, probe: stubProbe, remux: unusedRemux)
        #expect(second.sourcesUpdated == ["alpha.mov"])
        #expect(second.clipsDetected == 2)
        // Same source ID retained.
        let state = try store.fetchState()
        #expect(state.sources.count == 1)
        #expect(state.sources.values.first?.transcriptPath != nil)
    }
}

@MainActor @Test func filenamesWithSpacesAndSpecialCharsRoundTrip() async throws {
    try await withIngestFixture { store, media in
        let weirdNames = [
            "cuddlingchai content.mov",
            "is my face crooked??.mov",
            "more test videos <3.mov",
        ]
        for name in weirdNames {
            try touchVideo(in: media, name: name)
            try writeSidecar(in: media, stem: (name as NSString).deletingPathExtension)
        }

        let result = try await store.ingestFolder(at: media, probe: stubProbe, remux: unusedRemux)
        #expect(result.sourcesAdded.sorted() == weirdNames.sorted())

        // Round-trip through the backup JSON shape.
        let state = try store.fetchState()
        let data = try JSONEncoder().encode(state)
        let again = try JSONDecoder().decode(ClipFarmState.self, from: data)
        #expect(again.sources.values.map(\.filename).sorted() == weirdNames.sorted())
    }
}

@MainActor @Test func doubleDunderInDirectoryPathIsFine() async throws {
    try await withIngestFixture { store, media in
        // Only the filename stem is constrained — directory components with
        // `__` are fine.
        let sub = media.appendingPathComponent("session__1")
        try FileManager.default.createDirectory(at: sub, withIntermediateDirectories: true)
        try touchVideo(in: sub, name: "clean.mov")
        try writeSidecar(in: sub, stem: "clean")

        let result = try await store.ingestFolder(at: sub, probe: stubProbe, remux: unusedRemux)
        #expect(result.sourcesAdded == ["clean.mov"])
        #expect(result.rejected.isEmpty)
    }
}

@MainActor @Test func dottedStemHandledCorrectly() async throws {
    try await withIngestFixture { store, media in
        try touchVideo(in: media, name: "btc.0.4.mov")
        try writeSidecar(in: media, stem: "btc.0.4")

        let result = try await store.ingestFolder(at: media, probe: stubProbe, remux: unusedRemux)
        #expect(result.sourcesAdded == ["btc.0.4.mov"])
        #expect(result.clipsDetected == 2)
        let state = try store.fetchState()
        #expect(state.clips.keys.contains { $0.hasPrefix("btc.0.4\(ClipID.reservedSeparator)") })
    }
}

@MainActor @Test func notADirectoryThrows() async throws {
    try await withIngestFixture { store, media in
        await #expect(throws: IngestError.self) {
            _ = try await store.ingestFolder(
                at: media.appendingPathComponent("nope"), probe: stubProbe, remux: unusedRemux)
        }
    }
}

// MARK: - Native additions: .mkv remux (D15), probe fields (D17)

@MainActor @Test func mkvIsRemuxedAndRecordsProvenance() async throws {
    try await withIngestFixture { store, media in
        try touchVideo(in: media, name: "take.mkv")
        try writeSidecar(in: media, stem: "take")

        let result = try await store.ingestFolder(at: media, probe: stubProbe, remux: copyingRemux)

        #expect(result.sourcesAdded == ["take.mkv"])
        #expect(result.clipsDetected == 2)
        let source = try #require(try store.fetchState().sources.values.first)
        // `path` records the playable .mp4; the .mkv is provenance.
        #expect(source.path.hasSuffix("take.mp4"))
        #expect(source.filename == "take.mp4")
        #expect(source.originalPath?.hasSuffix("take.mkv") == true)
        // Same-stem sidecar serves the remuxed source.
        #expect(source.transcriptPath?.hasSuffix("take.whisper.json") == true)
    }
}

@MainActor @Test func mkvAndSiblingMP4DedupeToOneSource() async throws {
    try await withIngestFixture { store, media in
        // Both scanned; "a.mkv" sorts first, remuxes to a.mp4; the sibling
        // a.mp4 scan then resolves to an already-planned path.
        try touchVideo(in: media, name: "a.mkv")
        try touchVideo(in: media, name: "a.mp4")

        let result = try await store.ingestFolder(at: media, probe: stubProbe, remux: copyingRemux)

        #expect(result.sourcesAdded == ["a.mkv"])
        #expect(result.sourcesSkipped == ["a.mp4"])
        #expect(try store.fetchState().sources.count == 1)
    }
}

@MainActor @Test func failedRemuxRejectsTheFileAndContinues() async throws {
    try await withIngestFixture { store, media in
        try touchVideo(in: media, name: "broken.mkv")
        try touchVideo(in: media, name: "fine.mov")
        try writeSidecar(in: media, stem: "fine")

        struct StubRemuxError: Error {}
        let failingRemux: MKVRemux = { _ in throw StubRemuxError() }
        let result = try await store.ingestFolder(at: media, probe: stubProbe, remux: failingRemux)

        // PROVISIONAL 5: nothing playable exists -> reject, don't register.
        let rejection = try #require(result.rejected.first)
        #expect(rejection.reason == .remuxFailed)
        #expect(rejection.filename == "broken.mkv")
        #expect(result.sourcesAdded == ["fine.mov"])
        #expect(try store.fetchState().sources.count == 1)
    }
}

@MainActor @Test func probeFailureStillIngestsWithNilFields() async throws {
    try await withIngestFixture { store, media in
        try touchVideo(in: media, name: "unprobeable.mov")
        try writeSidecar(in: media, stem: "unprobeable", duration: 42.0)
        try touchVideo(in: media, name: "bare.mov")

        let result = try await store.ingestFolder(at: media, probe: failingProbe, remux: unusedRemux)
        #expect(result.sourcesAdded.count == 2)

        let state = try store.fetchState()
        let bySuffix: (String) -> Source? = { suffix in
            state.sources.values.first { $0.filename == suffix }
        }
        // Duration policy: sidecar wins -> probe -> nil.
        let withSidecar = try #require(bySuffix("unprobeable.mov"))
        #expect(withSidecar.durationSec == 42.0)
        #expect(withSidecar.fps == nil)
        let bare = try #require(bySuffix("bare.mov"))
        #expect(bare.durationSec == nil)
        #expect(bare.fps == nil)
        #expect(bare.isHDR == nil)
    }
}

@MainActor @Test func probeFieldsAreRecordedOnTheSource() async throws {
    try await withIngestFixture { store, media in
        try touchVideo(in: media, name: "hdr.mov")
        let richProbe: SourceProbe = { _ in
            ProbedSourceInfo(
                durationSec: 17.5, fps: 30.0, isHDR: true,
                naturalWidth: 1080, naturalHeight: 1920)
        }
        _ = try await store.ingestFolder(at: media, probe: richProbe, remux: unusedRemux)
        let source = try #require(try store.fetchState().sources.values.first)
        #expect(source.isHDR == true)
        #expect(source.naturalWidth == 1080)
        #expect(source.naturalHeight == 1920)
        #expect(source.fps == 30.0)
        #expect(source.durationSec == 17.5)
    }
}

// MARK: - Native additions: settings-driven segmentation (D18), FTS, undo

@MainActor @Test func segmentationReadsThresholdAndTailPolicyFromSettings() async throws {
    try await withIngestFixture { store, media in
        var settings = try store.librarySettings()
        settings.silenceThresholdSec = 0.4
        settings.tailPolicy = .wordEnd
        try store.updateLibrarySettings(settings)

        try touchVideo(in: media, name: "alpha.mov")
        try writeSidecar(in: media, stem: "alpha")

        // Default groups: intra-group gaps are 0.1 (< 0.4, no split); the
        // 3.0 inter-group gap splits -> still 2 clips, but word-end ranges.
        let result = try await store.ingestFolder(at: media, probe: stubProbe, remux: unusedRemux)
        #expect(result.clipsDetected == 2)
        let clips = try store.fetchState().clips
        #expect(clips["alpha__00-00-00.000__00-00-01.000"] != nil)
        #expect(clips["alpha__00-00-04.000__00-00-05.000"] != nil)
    }
}

@MainActor @Test func defaultTailPolicyExtendsEndsToNextWordStartAndDuration() async throws {
    try await withIngestFixture { store, media in
        try touchVideo(in: media, name: "alpha.mov")
        try writeSidecar(in: media, stem: "alpha", duration: 60.0)

        _ = try await store.ingestFolder(at: media, probe: stubProbe, remux: unusedRemux)
        let clips = try store.fetchState().clips
        // First clip's end extends to the next clip's first word (4.0); the
        // last clip extends to the sidecar duration (60.0).
        let first = try #require(clips["alpha__00-00-00.000__00-00-04.000"])
        #expect(first.endSec == 4.0)
        // Extended tail swallows no words: text is unchanged.
        #expect(first.transcriptText == "hello world")
        let last = try #require(clips["alpha__00-00-04.000__00-01-00.000"])
        #expect(last.endSec == 60.0)
        #expect(last.transcriptText == "second clip")
    }
}

@MainActor @Test func ingestedClipsAreImmediatelySearchableViaFTS() async throws {
    try await withIngestFixture { store, media in
        try touchVideo(in: media, name: "alpha.mov")
        try writeSidecar(in: media, stem: "alpha")

        _ = try await store.ingestFolder(at: media, probe: stubProbe, remux: unusedRemux)
        let hits = try await store.dbPool.read { db in
            try Int.fetchOne(
                db,
                sql: """
                    SELECT COUNT(*) FROM clips_fts
                    JOIN clips ON clips.rowid = clips_fts.rowid
                    WHERE clips_fts MATCH ?
                    """,
                arguments: ["\"hello world\""]
            ) ?? 0
        }
        #expect(hits == 1)
    }
}

@MainActor @Test func wholeIngestIsOneUndoStep() async throws {
    let undoManager = UndoManager()
    try await withIngestFixture(undoManager: undoManager) { store, media in
        try touchVideo(in: media, name: "alpha.mov")
        try writeSidecar(in: media, stem: "alpha")
        try touchVideo(in: media, name: "beta.mov")
        try writeSidecar(in: media, stem: "beta")

        _ = try await store.ingestFolder(at: media, probe: stubProbe, remux: unusedRemux)
        #expect(try store.fetchState().sources.count == 2)
        #expect(try store.fetchState().clips.count == 4)
        #expect(undoManager.undoActionName == "Ingest Folder")

        // One Cmd+Z takes the whole folder back out (PROVISIONAL 4)...
        undoManager.undo()
        let afterUndo = try store.fetchState()
        #expect(afterUndo.sources.isEmpty)
        #expect(afterUndo.clips.isEmpty)

        // ...and one redo restores it all.
        undoManager.redo()
        let afterRedo = try store.fetchState()
        #expect(afterRedo.sources.count == 2)
        #expect(afterRedo.clips.count == 4)
    }
}

@MainActor @Test func updateSourceRegistersUndoAndRedo() throws {
    let undoManager = UndoManager()
    try withScratchStore(undoManager: undoManager) { store in
        let id = try store.addSource(
            Source(filename: "a.mov", path: "/x/a.mov", addedAt: "t"))
        // Fresh group: the update undoes separately from the seed.
        undoManager.removeAllActions()

        var updated = try #require(try store.source(id: id))
        updated.transcriptPath = "/x/a.whisper.json"
        updated.durationSec = 60.0
        try store.updateSource(id: id, updated)

        #expect(undoManager.undoActionName == "Update Source")
        undoManager.undo()
        let reverted = try #require(try store.source(id: id))
        #expect(reverted.transcriptPath == nil)
        #expect(reverted.durationSec == nil)
        undoManager.redo()
        let redone = try #require(try store.source(id: id))
        #expect(redone.transcriptPath == "/x/a.whisper.json")
        #expect(redone.durationSec == 60.0)
    }
}

@MainActor @Test func updateSourceUnknownIDThrows() throws {
    try withScratchStore { store in
        #expect(throws: LibraryStoreError.unknownSourceID("404")) {
            try store.updateSource(
                id: "404", Source(filename: "x.mov", path: "/x.mov", addedAt: "t"))
        }
    }
}
