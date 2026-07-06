import CFDomain
import CFTestSupport
import Foundation
import GRDB
import Testing
@testable import CFStore

/// The FTS5 external-content index must follow every clip mutation
/// (insert/update/delete triggers) — search must never surface deleted
/// clips or stale transcript text. The search feature itself lands at N4;
/// these tests pin the trigger contract underneath it.

private func matches(_ store: LibraryStore, _ query: String) throws -> Int {
    try store.dbPool.read { db in
        try Int.fetchOne(
            db,
            sql: "SELECT COUNT(*) FROM clips_fts WHERE clips_fts MATCH ?",
            arguments: [query]
        ) ?? -1
    }
}

@MainActor @Test func insertedClipsAreImmediatelySearchable() throws {
    try withScratchStore { store in
        try store.addSource(Fixtures.source(), id: "s1")
        try store.addClips([(
            id: "c1",
            clip: Fixtures.clip(sourceID: "s1", startSec: 0, endSec: 5,
                                transcriptText: " keep your keys in self custody")
        )])
        #expect(try matches(store, "custody") == 1)
        // FTS5 phrase queries work — the N4 upgrade the web never had.
        #expect(try matches(store, "\"self custody\"") == 1)
        #expect(try matches(store, "bitcoin") == 0)
    }
}

@MainActor @Test func updatedTranscriptTextReindexes() throws {
    try withScratchStore { store in
        try store.addSource(Fixtures.source(), id: "s1")
        try store.addClips([(
            id: "c1",
            clip: Fixtures.clip(sourceID: "s1", startSec: 0, endSec: 5, transcriptText: " old words")
        )])
        try store.dbPool.write { db in
            try db.execute(sql: "UPDATE clips SET transcript_text = ' new words' WHERE id = 'c1'")
        }
        #expect(try matches(store, "old") == 0, "stale text must leave the index")
        #expect(try matches(store, "new") == 1)
    }
}

@MainActor @Test func deletedClipsLeaveTheIndex() throws {
    try withScratchStore { store in
        try store.addSource(Fixtures.source(), id: "s1")
        try store.addClips([(
            id: "c1",
            clip: Fixtures.clip(sourceID: "s1", startSec: 0, endSec: 5, transcriptText: " going away")
        )])
        try store.performDestructive(reason: "delete-clip") { db in
            try db.execute(sql: "DELETE FROM clips WHERE id = 'c1'")
        }
        #expect(try matches(store, "away") == 0, "search must never surface deleted clips")
    }
}
