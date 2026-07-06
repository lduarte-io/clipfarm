import CFDomain
import CFTestSupport
import Foundation
import GRDB
import Testing
@testable import CFStore

/// The D18 per-source "Re-apply segmentation settings" action: skips
/// `boundary_edited` clips, ID-preserving diff (PROVISIONAL 3),
/// snapshot-protected, undoable, delete-propagation flags applied forward.

/// One source (id "1") with a transcript of four words in two 3s-separated
/// groups, ingested under word-end policy -> clips at (0,1) and (4,5).
@MainActor
private func seedIngestedSource(
    _ store: LibraryStore
) async throws -> (sourceID: String, transcript: WhisperTranscript) {
    var settings = try store.librarySettings()
    settings.tailPolicy = .wordEnd
    try store.updateLibrarySettings(settings)

    let transcript = Fixtures.transcript(words: [
        (start: 0.0, end: 0.5, word: " hello"),
        (start: 0.6, end: 1.0, word: " world"),
        (start: 4.0, end: 4.5, word: " second"),
        (start: 4.6, end: 5.0, word: " clip"),
    ])
    let sourceID = try store.addSource(
        Source(filename: "alpha.mov", path: "/x/alpha.mov", durationSec: 60.0, addedAt: "t"))
    var existing = Set<String>()
    let clips = try LibraryStore.plannedClips(
        for: transcript, stem: "alpha", sourceID: sourceID, durationSec: 60.0,
        settings: try store.librarySettings(), createdAt: "t", existingIDs: &existing)
    try store.addClips(clips)
    return (sourceID, transcript)
}

@MainActor @Test func flippingTailPolicyReplacesAutoDetectedClips() async throws {
    try await withIngestFixture { store, _ in
        let (sourceID, transcript) = try await seedIngestedSource(store)
        #expect(try store.fetchState().clips.count == 2)

        var settings = try store.librarySettings()
        settings.tailPolicy = .extendToNextWordStart
        try store.updateLibrarySettings(settings)

        let result = try store.reapplySegmentation(forSourceID: sourceID, transcript: transcript)
        #expect(result.changed)
        #expect(result.clipsRemoved == 2)
        #expect(result.clipsAdded == 2)
        #expect(result.clipsKept == 0)

        let clips = try store.fetchState().clips
        #expect(clips.count == 2)
        #expect(clips["alpha__00-00-00.000__00-00-04.000"]?.endSec == 4.0)
        #expect(clips["alpha__00-00-04.000__00-01-00.000"]?.endSec == 60.0)
    }
}

@MainActor @Test func reapplyWithUnchangedSettingsIsANoOpAndTakesNoSnapshot() async throws {
    try await withIngestFixture { store, _ in
        let (sourceID, transcript) = try await seedIngestedSource(store)
        let snapshotsBefore = store.listSnapshots().count

        let result = try store.reapplySegmentation(forSourceID: sourceID, transcript: transcript)
        #expect(!result.changed)
        #expect(result.clipsKept == 2)
        // A true no-op is not a destructive op: no snapshot, no undo entry.
        #expect(store.listSnapshots().count == snapshotsBefore)
    }
}

@MainActor @Test func boundaryEditedClipsAreNeverTouched() async throws {
    try await withIngestFixture { store, _ in
        let (sourceID, transcript) = try await seedIngestedSource(store)
        // Hand-correct the first clip (N5 ops set this flag; simulate).
        try await store.dbPool.write { db in
            try db.execute(
                sql: "UPDATE clips SET boundary_edited = 1, start_sec = 0.1 WHERE id = ?",
                arguments: ["alpha__00-00-00.000__00-00-01.000"])
        }

        var settings = try store.librarySettings()
        settings.tailPolicy = .extendToNextWordStart
        try store.updateLibrarySettings(settings)

        let result = try store.reapplySegmentation(forSourceID: sourceID, transcript: transcript)
        #expect(result.skippedBoundaryEdited == 1)
        // Only the auto-detected clip was replaced.
        #expect(result.clipsRemoved == 1)
        #expect(result.clipsAdded == 2)

        let clips = try store.fetchState().clips
        let handEdited = try #require(clips["alpha__00-00-00.000__00-00-01.000"])
        #expect(handEdited.startSec == 0.1)
        #expect(handEdited.boundaryEdited)
    }
}

@MainActor @Test func unchangedRecomputedRangesKeepTheirRowsAndTags() async throws {
    try await withIngestFixture { store, _ in
        let (sourceID, transcript) = try await seedIngestedSource(store)
        // Tag the second clip; its recomputed range will be unchanged when
        // only the FIRST clip's range moves... here we flip tail policy,
        // which changes both ends — so instead change only the threshold in
        // a way that preserves both ranges and adds nothing: a no-op keep.
        // The load-bearing case: recomputed IDs that already exist are kept,
        // so their tag rows survive.
        let keptID = "alpha__00-00-04.000__00-00-05.000"
        try store.addClipProjectTag(ClipProjectTag(
            clipID: keptID, projectID: "p1", category: .standaloneIdea))

        // Threshold 0.4: intra-group gaps (0.1) still don't split; ranges
        // identical -> everything kept, nothing destroyed.
        var settings = try store.librarySettings()
        settings.silenceThresholdSec = 0.4
        try store.updateLibrarySettings(settings)

        let result = try store.reapplySegmentation(forSourceID: sourceID, transcript: transcript)
        #expect(!result.changed)
        #expect(result.clipsKept == 2)
        let tagCount = try await store.dbPool.read { db in
            try Int.fetchOne(
                db, sql: "SELECT COUNT(*) FROM clip_project_tags WHERE clip_id = ?",
                arguments: [keptID]) ?? 0
        }
        #expect(tagCount == 1)
    }
}

@MainActor @Test func reapplyIsSnapshotProtectedAndUndoable() async throws {
    let undoManager = UndoManager()
    try await withIngestFixture(undoManager: undoManager) { store, _ in
        let (sourceID, transcript) = try await seedIngestedSource(store)
        // Fresh undo group: the re-apply undoes separately from the seed
        // (UndoManager groups by run-loop turn, and tests have none).
        undoManager.removeAllActions()
        let before = try store.fetchState()
        let snapshotsBefore = store.listSnapshots().count

        var settings = try store.librarySettings()
        settings.tailPolicy = .extendToNextWordStart
        try store.updateLibrarySettings(settings)
        _ = try store.reapplySegmentation(forSourceID: sourceID, transcript: transcript)

        // Snapshot fired once, with the named reason.
        let snapshots = store.listSnapshots()
        #expect(snapshots.count == snapshotsBefore + 1)
        #expect(snapshots.first?.lastPathComponent.contains("re-apply-segmentation") == true)
        #expect(undoManager.undoActionName == "Re-apply Segmentation")

        // Undo restores the exact prior clip table (IDs, ranges, texts)...
        undoManager.undo()
        #expect(try store.fetchState() == before)
        // ...without taking another snapshot (undo of a destructive op is
        // not itself snapshot-worthy).
        #expect(store.listSnapshots().count == snapshotsBefore + 1)

        // Redo re-applies.
        undoManager.redo()
        let redone = try store.fetchState()
        #expect(redone.clips["alpha__00-00-00.000__00-00-04.000"] != nil)
        #expect(redone.clips.count == 2)
    }
}

@MainActor @Test func undoRestoresTagRowsOfDeletedClips() async throws {
    let undoManager = UndoManager()
    try await withIngestFixture(undoManager: undoManager) { store, _ in
        let (sourceID, transcript) = try await seedIngestedSource(store)
        let taggedID = "alpha__00-00-00.000__00-00-01.000"
        try store.addClipProjectTag(ClipProjectTag(
            clipID: taggedID, projectID: "p1", category: .onScript, confidence: 0.9))
        undoManager.removeAllActions()

        var settings = try store.librarySettings()
        settings.tailPolicy = .extendToNextWordStart
        try store.updateLibrarySettings(settings)
        let result = try store.reapplySegmentation(forSourceID: sourceID, transcript: transcript)
        #expect(result.clipsRemoved == 2)
        // The tag row went with its clip (spec clip-delete rule).
        let tagsAfter = try await store.dbPool.read { db in
            try Int.fetchOne(db, sql: "SELECT COUNT(*) FROM clip_project_tags") ?? 0
        }
        #expect(tagsAfter == 0)

        undoManager.undo()
        let confidence = try await store.dbPool.read { db in
            try Double.fetchOne(
                db, sql: "SELECT confidence FROM clip_project_tags WHERE clip_id = ?",
                arguments: [taggedID])
        }
        #expect(confidence == 0.9)
        let category = try await store.dbPool.read { db in
            try String.fetchOne(
                db, sql: "SELECT category FROM clip_project_tags WHERE clip_id = ?",
                arguments: [taggedID])
        }
        #expect(category == "on-script")
    }
}

@MainActor @Test func attemptsReferencingDeletedClipsGetNeedsReview() async throws {
    let undoManager = UndoManager()
    try await withIngestFixture(undoManager: undoManager) { store, _ in
        let (sourceID, transcript) = try await seedIngestedSource(store)
        // An attempt referencing the first auto-detected clip (N9 territory,
        // seeded directly — the delete-propagation rule is spec behavior).
        try await store.dbPool.write { db in
            try AttemptRecord(
                id: "1",
                attempt: Fixtures.attempt(
                    clips: [AttemptClip(clipID: "alpha__00-00-00.000__00-00-01.000")])
            ).insert(db)
            try AttemptClipRecord(
                attemptID: "1", position: 0,
                attemptClip: AttemptClip(clipID: "alpha__00-00-00.000__00-00-01.000")
            ).insert(db)
        }

        undoManager.removeAllActions()
        var settings = try store.librarySettings()
        settings.tailPolicy = .extendToNextWordStart
        try store.updateLibrarySettings(settings)
        _ = try store.reapplySegmentation(forSourceID: sourceID, transcript: transcript)

        let flagged = try await store.dbPool.read { db in
            try Bool.fetchOne(db, sql: "SELECT needs_review FROM attempts WHERE id = '1'")
        }
        #expect(flagged == true)
        // The attempt-clip ref dangles by design (tombstone pattern).
        let refCount = try await store.dbPool.read { db in
            try Int.fetchOne(db, sql: "SELECT COUNT(*) FROM attempt_clips WHERE attempt_id = '1'") ?? 0
        }
        #expect(refCount == 1)

        // Undo restores the attempt's needs_review before-value.
        undoManager.undo()
        let unflagged = try await store.dbPool.read { db in
            try Bool.fetchOne(db, sql: "SELECT needs_review FROM attempts WHERE id = '1'")
        }
        #expect(unflagged == false)
    }
}

@MainActor @Test func insertionsOnlyDiffAppliesCleanlyAndUndoes() async throws {
    // Cold-review finding 1 regression: EVERY existing clip hand-corrected
    // (boundary_edited), then a settings change -> zero deletions, pure
    // insertions. Must apply (snapshot + undo) — not throw on an empty
    // undo-capture IN () query.
    let undoManager = UndoManager()
    try await withIngestFixture(undoManager: undoManager) { store, _ in
        let (sourceID, transcript) = try await seedIngestedSource(store)
        try await store.dbPool.write { db in
            try db.execute(sql: "UPDATE clips SET boundary_edited = 1")
        }
        undoManager.removeAllActions()

        var settings = try store.librarySettings()
        settings.tailPolicy = .extendToNextWordStart
        try store.updateLibrarySettings(settings)

        let result = try store.reapplySegmentation(forSourceID: sourceID, transcript: transcript)
        #expect(result.clipsRemoved == 0)
        #expect(result.clipsAdded == 2)
        #expect(result.skippedBoundaryEdited == 2)
        #expect(try store.fetchState().clips.count == 4)
        #expect(undoManager.undoActionName == "Re-apply Segmentation")

        undoManager.undo()
        #expect(try store.fetchState().clips.count == 2)
        undoManager.redo()
        #expect(try store.fetchState().clips.count == 4)
    }
}

@MainActor @Test func reapplyErrorsAreTyped() async throws {
    try await withIngestFixture { store, _ in
        #expect(throws: ReapplySegmentationError.unknownSource("404")) {
            try store.reapplySegmentation(forSourceID: "404")
        }
        let id = try store.addSource(
            Source(filename: "bare.mov", path: "/x/bare.mov", addedAt: "t"))
        #expect(throws: ReapplySegmentationError.noTranscript(sourceID: id)) {
            try store.reapplySegmentation(forSourceID: id)
        }
    }
}

// MARK: - Real-inbox end-to-end golden master (plan §4/N3 verify leg)

private let footageInbox = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("ClipFarm/Footage")

private let webProcessedStems = ["btc.0.2", "btc.0.4", "freestylingbtc0"]
private let webClipCounts = ["btc.0.2": 10, "btc.0.4": 91, "freestylingbtc0": 8]

private var inboxGoldenMaterialPresent: Bool {
    webProcessedStems.allSatisfy {
        FileManager.default.fileExists(
            atPath: footageInbox.appendingPathComponent("\($0).whisper.json").path)
            && FileManager.default.fileExists(
                atPath: footageInbox.appendingPathComponent("\($0).mov").path)
    }
}

/// End-to-end: `ingestFolder` over the REAL footage inbox (read-only; the
/// library is a temp scratch, the probe is stubbed so no AVFoundation churn
/// on multi-GB files). Per-source clip counts must match the web version's
/// legacy state — the plan's "counts match" verify, automated. Tail policy =
/// word-end for comparability.
@MainActor @Test(.enabled(if: inboxGoldenMaterialPresent))
func ingestingTheRealInboxMatchesWebClipCounts() async throws {
    try await withIngestFixture { store, _ in
        var settings = try store.librarySettings()
        settings.tailPolicy = .wordEnd
        try store.updateLibrarySettings(settings)

        let result = try await store.ingestFolder(
            at: footageInbox, probe: failingProbe, remux: unusedRemux)

        #expect(result.rejected.isEmpty)
        let state = try store.fetchState()
        for (stem, expectedCount) in webClipCounts {
            let source = try #require(
                state.sources.first { $0.value.filename == "\(stem).mov" },
                "\(stem).mov should have been ingested")
            let count = state.clips.values.count { $0.sourceID == source.key }
            #expect(count == expectedCount, "\(stem): clip count vs web version")
        }
        // Everything else in the inbox is sidecar-less -> footage-only.
        let transcriptless = state.sources.values.count { $0.transcriptPath == nil }
        #expect(transcriptless == state.sources.count - webProcessedStems.count)
        #expect(result.clipsDetected == webClipCounts.values.reduce(0, +))
    }
}
