import CFDomain
import Foundation
import GRDB

/// The only seam to the library database (mac/CLAUDE.md invariant: all DB
/// access goes through CFStore; nothing outside this module imports GRDB).
///
/// A library is a visible folder (default `~/ClipFarm/`, D28) containing
/// `clipfarm.db` (GRDB 7, WAL) and `.snapshots/`. Opening migrates via
/// `DatabaseMigrator` (v1 registered from day one), refuses a library
/// written by a newer app, and runs the source-integrity check.
///
/// Undo: the store takes an **injected** `UndoManager` (Foundation — Kit
/// tests drive it directly; the app vends the window's instance). The store
/// never reaches for UI.
public final class LibraryStore {
    public static let databaseFilename = "clipfarm.db"
    public static let snapshotsDirectoryName = ".snapshots"
    public static let snapshotLimit = 50

    /// The default library location (D28). Overridable in Settings (N7+).
    public static var defaultLibraryFolderURL: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("ClipFarm")
    }

    public let folderURL: URL
    public let databaseURL: URL
    /// Read at every undo registration, so a late adoption (see
    /// `adoptUndoManager`) covers all subsequent mutations. Written only
    /// from `@MainActor` contexts (init-time injection or adoption); every
    /// reader is a `@MainActor` undo path.
    public private(set) var undoManager: UndoManager?

    let dbPool: DatabasePool
    let now: @Sendable () -> Date

    private init(
        folderURL: URL,
        databaseURL: URL,
        dbPool: DatabasePool,
        undoManager: UndoManager?,
        now: @escaping @Sendable () -> Date
    ) {
        self.folderURL = folderURL
        self.databaseURL = databaseURL
        self.dbPool = dbPool
        self.undoManager = undoManager
        self.now = now
    }

    /// Opens (creating if needed) the library at `folderURL`.
    ///
    /// Steps: create folder → open `DatabasePool` (WAL) → refuse a
    /// superseded schema → migrate → stamp `meta.created_at` once →
    /// source-integrity check (missing files flip `unavailable`, never
    /// crash the load).
    public static func open(
        at folderURL: URL,
        undoManager: UndoManager? = nil,
        now: @escaping @Sendable () -> Date = Date.init
    ) throws -> LibraryStore {
        try FileManager.default.createDirectory(at: folderURL, withIntermediateDirectories: true)
        let databaseURL = folderURL.appendingPathComponent(databaseFilename)
        let dbPool = try DatabasePool(path: databaseURL.path)

        // Any throw past this point must close the pool — a leaked pool
        // holds file handles until deinit, and the close→swap→reopen path
        // exists precisely to make reopening the same folder reliable
        // (cold-review finding 5).
        do {
            let migrator = LibrarySchema.migrator()
            let superseded = try dbPool.read { db in
                try migrator.hasBeenSuperseded(db)
            }
            guard !superseded else {
                throw LibraryStoreError.librarySupersededByNewerApp(databaseURL: databaseURL)
            }
            try migrator.migrate(dbPool)

            let store = LibraryStore(
                folderURL: folderURL,
                databaseURL: databaseURL,
                dbPool: dbPool,
                undoManager: undoManager,
                now: now
            )
            try store.stampCreatedAtIfNeeded()
            try store.runSourceIntegrityCheck()
            return store
        } catch {
            try? dbPool.close()
            throw error
        }
    }

    /// Closes the underlying database. The store is unusable afterward;
    /// library switching goes through `LibraryManager`, which also clears
    /// the undo stack (its closures capture this store).
    public func close() throws {
        try dbPool.close()
    }

    /// Adopts a window `UndoManager` when the store was opened without one
    /// (SwiftUI's environment value can materialize after the first view
    /// task — cold-review finding 4). First adoption wins; mutations made
    /// before it simply aren't undoable, and later ones all are.
    @MainActor
    public func adoptUndoManager(_ manager: UndoManager) {
        guard undoManager == nil else { return }
        undoManager = manager
    }

    // MARK: - Open-time rituals

    private func stampCreatedAtIfNeeded() throws {
        try dbPool.write { db in
            let existing = try String.fetchOne(
                db, sql: "SELECT value FROM meta WHERE key = 'created_at'"
            )
            if existing == nil {
                try db.execute(
                    sql: "INSERT INTO meta(key, value) VALUES ('created_at', ?)",
                    arguments: [iso8601(now())]
                )
            }
        }
    }

    /// Flips `unavailable` for any source whose `path` no longer resolves to
    /// a file — and back, when a file reappears. Run on every open (and on
    /// Library refresh, later). Tags and attempt references stay intact;
    /// only playback is gated on availability.
    public func runSourceIntegrityCheck() throws {
        try dbPool.write { db in
            let rows = try Row.fetchAll(db, sql: "SELECT id, path, unavailable FROM sources")
            for row in rows {
                let path: String = row["path"]
                let flagged: Bool = row["unavailable"]
                let exists = Self.isFile(atPath: path)
                if flagged == exists {
                    try db.execute(
                        sql: "UPDATE sources SET unavailable = ? WHERE id = ?",
                        arguments: [!exists, row["id"] as String]
                    )
                }
            }
        }
    }

    private static func isFile(atPath path: String) -> Bool {
        var isDirectory: ObjCBool = false
        let exists = FileManager.default.fileExists(atPath: path, isDirectory: &isDirectory)
        return exists && !isDirectory.boolValue
    }

    func iso8601(_ date: Date) -> String {
        date.ISO8601Format(.iso8601)
    }
}

public enum LibraryStoreError: Error, Equatable {
    /// The database contains migrations this app doesn't know — it was
    /// written by a newer ClipFarm. Refuse rather than corrupt.
    case librarySupersededByNewerApp(databaseURL: URL)
    /// A clip insert referenced an ID that already exists (clip IDs are
    /// allocated once, at creation).
    case duplicateClipID(String)
    /// An update targeted a source ID that doesn't exist.
    case unknownSourceID(String)
}
