import CFDomain
import Foundation
import GRDB
import Testing
@testable import CFStore

/// Port of `tests/test_migrations.py` (4 tests), adapted to GRDB's
/// `DatabaseMigrator`: v1 registered from day one, idempotent reopen,
/// refuse-newer (the analog of "future version refuses to downgrade"),
/// in-order application when later migrations land. N3 adds the first real
/// upgrade (v2: `sources.original_path`) and its dedicated tests.

@Test func freshLibraryAppliesAllMigrations() throws {
    try withScratchStoreNonisolated { store in
        let applied = try store.dbPool.read { db in
            try LibrarySchema.migrator().appliedMigrations(db)
        }
        #expect(applied == ["v1", "v2"])
    }
}

@Test func reopeningAtCurrentVersionIsANoop() throws {
    let folder = try makeScratchFolder()
    defer { try? FileManager.default.removeItem(at: folder) }
    let first = try LibraryStore.open(at: folder)
    try first.close()
    // Second open re-runs the migrator; nothing new applies, no error.
    let second = try LibraryStore.open(at: folder)
    defer { try? second.close() }
    let applied = try second.dbPool.read { db in
        try LibrarySchema.migrator().appliedMigrations(db)
    }
    #expect(applied == ["v1", "v2"])
}

@Test func futureVersionLibraryRefusesToOpen() throws {
    // The analog of the reference's refuse-to-downgrade rule: a library
    // carrying migrations this build doesn't know is from a newer app.
    let folder = try makeScratchFolder()
    defer { try? FileManager.default.removeItem(at: folder) }
    let store = try LibraryStore.open(at: folder)
    try store.dbPool.write { db in
        try db.execute(sql: "INSERT INTO grdb_migrations (identifier) VALUES ('v99')")
    }
    try store.close()

    #expect {
        _ = try LibraryStore.open(at: folder)
    } throws: { error in
        guard case LibraryStoreError.librarySupersededByNewerApp = error else { return false }
        return true
    }
}

@Test func v1LibraryUpgradesToV2PreservingRows() throws {
    // Build a genuine v1-only database (migrate(upTo:)), populate it, then
    // full-migrate: the source row survives, the new column reads NULL, and
    // the informational meta stamp advances 1 → 2.
    let folder = try makeScratchFolder()
    defer { try? FileManager.default.removeItem(at: folder) }
    let dbURL = folder.appendingPathComponent(LibraryStore.databaseFilename)
    let queue = try DatabaseQueue(path: dbURL.path)
    try LibrarySchema.migrator().migrate(queue, upTo: "v1")
    try queue.write { db in
        try db.execute(sql: """
            INSERT INTO sources(id, filename, path, added_at, unavailable)
            VALUES ('1', 'a.mov', '/x/a.mov', 't', 0)
            """)
        let stamp = try String.fetchOne(db, sql: "SELECT value FROM meta WHERE key = 'schema_version'")
        #expect(stamp == "1")
        let hasColumn = try db.columns(in: "sources").contains { $0.name == "original_path" }
        #expect(!hasColumn)
    }
    try LibrarySchema.migrator().migrate(queue)
    try queue.read { db in
        let hasColumn = try db.columns(in: "sources").contains { $0.name == "original_path" }
        #expect(hasColumn)
        let row = try Row.fetchOne(db, sql: "SELECT filename, original_path FROM sources WHERE id = '1'")
        #expect(row?["filename"] == "a.mov")
        #expect((row?["original_path"] as String?) == nil)
        let stamp = try String.fetchOne(db, sql: "SELECT value FROM meta WHERE key = 'schema_version'")
        #expect(stamp == "2")
    }
    try queue.close()
}

@Test func originalPathRoundTripsThroughSourceRecord() throws {
    try withScratchStoreNonisolated { store in
        try store.dbPool.write { db in
            try SourceRecord(
                id: "1",
                source: Source(
                    filename: "cam.mp4",
                    path: "/footage/cam.mp4",
                    originalPath: "/footage/cam.mkv",
                    addedAt: "t"
                )
            ).insert(db)
        }
        let fetched = try store.source(id: "1")
        #expect(fetched?.originalPath == "/footage/cam.mkv")
        #expect(fetched?.path == "/footage/cam.mp4")
    }
}

@Test func laterMigrationsApplyInRegistrationOrder() throws {
    // When v2 lands it extends LibrarySchema.migrator() — prove that a
    // v1 database picks up exactly the delta, in order.
    let folder = try makeScratchFolder()
    defer { try? FileManager.default.removeItem(at: folder) }
    let store = try LibraryStore.open(at: folder)
    try store.close()

    var future = LibrarySchema.migrator()
    future.registerMigration("v-next-test") { db in
        try db.execute(sql: "CREATE TABLE vnext_marker(id INTEGER PRIMARY KEY)")
    }
    let queue = try DatabaseQueue(path: folder.appendingPathComponent(LibraryStore.databaseFilename).path)
    defer { try? queue.close() }
    try future.migrate(queue)
    let applied = try queue.read { db in
        try future.appliedMigrations(db)
    }
    #expect(applied == ["v1", "v2", "v-next-test"])
    let hasMarker = try queue.read { db in
        try db.tableExists("vnext_marker")
    }
    #expect(hasMarker)
}

/// Nonisolated twin of `withScratchStore` for tests that never touch the
/// (MainActor) undo path.
@discardableResult
func withScratchStoreNonisolated<T>(
    _ body: (LibraryStore) throws -> T
) throws -> T {
    let folder = try makeScratchFolder()
    defer { try? FileManager.default.removeItem(at: folder) }
    let store = try LibraryStore.open(at: folder)
    defer { try? store.close() }
    return try body(store)
}
