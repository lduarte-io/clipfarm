import CFDomain
import CFTestSupport
import Foundation
import GRDB
import Testing
@testable import CFStore

/// The pre-destructive-op snapshot ritual (`VACUUM INTO`, prune to 50) —
/// port of the `test_store.py` snapshot suite plus the native mechanics:
/// snapshot + mutation in one barrier (finding 11), partial-file cleanup,
/// undo-is-not-snapshot-worthy.
///
/// Recorded divergence: the reference's "no state file yet → snapshot is a
/// no-op returning None" case doesn't port — an open library always has a
/// database file, so a snapshot always lands.

/// Reads a snapshot .db file back as a plain database and counts clips.
private func clipCount(inSnapshot url: URL) throws -> Int {
    let queue = try DatabaseQueue(path: url.path)
    defer { try? queue.close() }
    return try queue.read { db in
        try Int.fetchOne(db, sql: "SELECT COUNT(*) FROM clips") ?? -1
    }
}

@MainActor @Test func snapshotCapturesTheCurrentStateAsAValidDatabase() throws {
    try withScratchStore { store in
        try store.importState(makeState(clipCount: 2))
        let snapshot = try store.snapshotBeforeDestructive(reason: "split-clip")
        #expect(snapshot.deletingLastPathComponent() == store.snapshotsDirectoryURL)
        #expect(try clipCount(inSnapshot: snapshot) == 2)
    }
}

@MainActor @Test func performDestructiveSnapshotsThePreChangeState() throws {
    // The snapshot must hold the state from BEFORE the mutation; the main
    // database holds the state after. One barrier access covers both.
    try withScratchStore { store in
        try store.importState(makeState(clipCount: 1))
        try store.performDestructive(reason: "test-destructive") { db in
            try db.execute(sql: "DELETE FROM clip_project_tags")
            try db.execute(sql: "DELETE FROM clips")
        }
        let snapshot = try #require(store.listSnapshots().first)
        #expect(try clipCount(inSnapshot: snapshot) == 1, "snapshot must be pre-change")
        #expect(try store.fetchState().clips.isEmpty, "main must reflect the mutation")
    }
}

@MainActor @Test func performDestructiveRollsBackTheMutationOnFailure() throws {
    struct Boom: Error {}
    try withScratchStore { store in
        try store.importState(makeState(clipCount: 2))
        #expect(throws: Boom.self) {
            try store.performDestructive(reason: "explodes") { db in
                try db.execute(sql: "DELETE FROM clip_project_tags")
                try db.execute(sql: "DELETE FROM clips")
                throw Boom()
            }
        }
        // Transaction rolled back; the pre-op snapshot still exists.
        #expect(try store.fetchState().clips.count == 2)
        #expect(store.listSnapshots().count == 1)
    }
}

@MainActor @Test func snapshotsPruneToTheLimit() throws {
    try withScratchStore { store in
        try store.importState(makeState(clipCount: 1))
        for i in 0..<(LibraryStore.snapshotLimit + 5) {
            try store.snapshotBeforeDestructive(reason: "op-\(i)")
        }
        #expect(store.listSnapshots().count == LibraryStore.snapshotLimit)
    }
}

@MainActor @Test func snapshotReasonIsSanitizedForFilenames() throws {
    try withScratchStore { store in
        let snapshot = try store.snapshotBeforeDestructive(reason: "split clip / mid sentence!")
        let name = snapshot.lastPathComponent
        #expect(!name.contains(" "))
        #expect(!name.contains("/"))
        #expect(name.hasSuffix(".db"))
        #expect(name.contains("split-clip-mid-sentence"))
    }
}

@Test func safeLabelCollapsesRunsAndDefaultsToSnapshot() {
    #expect(LibraryStore.safeLabel("split clip / mid!") == "split-clip-mid-")
    #expect(LibraryStore.safeLabel("retag_clobber.v2") == "retag_clobber.v2")
    #expect(LibraryStore.safeLabel("  ") == "snapshot")
}

@MainActor @Test func snapshotsInTheSameMillisecondGetDistinctFilenames() throws {
    // Frozen clock → identical ISO+ms prefix; the 4-hex token must still
    // keep the filenames distinct.
    let frozen = Date(timeIntervalSince1970: 1_780_000_000.123)
    try withScratchStore(now: { frozen }) { store in
        let a = try store.snapshotBeforeDestructive(reason: "op")
        let b = try store.snapshotBeforeDestructive(reason: "op")
        #expect(a.lastPathComponent != b.lastPathComponent)
        #expect(store.listSnapshots().count == 2)
    }
}

@MainActor @Test func failedSnapshotNeverDestroysAPreexistingFile() throws {
    // VACUUM INTO refuses an existing target. On that failure the cleanup
    // must NOT remove the pre-existing file — a same-millisecond filename
    // collision with an older good snapshot must never destroy it (the
    // cleanup only removes a file the failed VACUUM itself created).
    try withScratchStore { store in
        try FileManager.default.createDirectory(
            at: store.snapshotsDirectoryURL, withIntermediateDirectories: true
        )
        let target = store.snapshotsDirectoryURL.appendingPathComponent("preexisting.db")
        let originalContents = Data("an older, still-good snapshot".utf8)
        try originalContents.write(to: target)

        #expect(throws: DatabaseError.self) {
            try store.dbPool.writeWithoutTransaction { db in
                try store.writeSnapshot(db, to: target)
            }
        }
        // The error propagated AND the pre-existing file survived intact.
        #expect(try Data(contentsOf: target) == originalContents)
    }
}

@MainActor @Test func undoOfAMutationDoesNotTakeASnapshot() throws {
    // The pre-op snapshot already covers the pre-op state; undo replays
    // plain writes and must never mint new snapshots.
    let undoManager = UndoManager()
    try withScratchStore(undoManager: undoManager) { store in
        try store.addSource(Fixtures.source())
        #expect(store.listSnapshots().isEmpty)
        undoManager.undo()
        undoManager.redo()
        #expect(store.listSnapshots().isEmpty)
    }
}
