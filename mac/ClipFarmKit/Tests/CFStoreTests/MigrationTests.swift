import Foundation
import GRDB
import Testing
@testable import CFStore

/// Port of `tests/test_migrations.py` (4 tests), adapted to GRDB's
/// `DatabaseMigrator`: v1 registered from day one, idempotent reopen,
/// refuse-newer (the analog of "future version refuses to downgrade"),
/// in-order application when later migrations land.

@Test func freshLibraryAppliesV1() throws {
    try withScratchStoreNonisolated { store in
        let applied = try store.dbPool.read { db in
            try LibrarySchema.migrator().appliedMigrations(db)
        }
        #expect(applied == ["v1"])
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
    #expect(applied == ["v1"])
}

@Test func futureVersionLibraryRefusesToOpen() throws {
    // The analog of the reference's refuse-to-downgrade rule: a library
    // carrying migrations this build doesn't know is from a newer app.
    let folder = try makeScratchFolder()
    defer { try? FileManager.default.removeItem(at: folder) }
    let store = try LibraryStore.open(at: folder)
    try store.dbPool.write { db in
        try db.execute(sql: "INSERT INTO grdb_migrations (identifier) VALUES ('v2')")
    }
    try store.close()

    #expect {
        _ = try LibraryStore.open(at: folder)
    } throws: { error in
        guard case LibraryStoreError.librarySupersededByNewerApp = error else { return false }
        return true
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
    future.registerMigration("v2-test") { db in
        try db.execute(sql: "CREATE TABLE v2_marker(id INTEGER PRIMARY KEY)")
    }
    let queue = try DatabaseQueue(path: folder.appendingPathComponent(LibraryStore.databaseFilename).path)
    defer { try? queue.close() }
    try future.migrate(queue)
    let applied = try queue.read { db in
        try future.appliedMigrations(db)
    }
    #expect(applied == ["v1", "v2-test"])
    let hasMarker = try queue.read { db in
        try db.tableExists("v2_marker")
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
