import Foundation
import GRDB

/// The pre-destructive-op snapshot ritual (spec: every destructive operation
/// writes a snapshot first; native amendment #3: `VACUUM INTO`, keep 50).
///
/// SQLite mechanics (finding 11): `VACUUM INTO` cannot run inside an open
/// transaction, so `performDestructive` executes the snapshot and the
/// mutating transaction inside ONE `writeWithoutTransaction` barrier —
/// snapshot first (no transaction), then `BEGIN…COMMIT`. No other writer
/// can interleave between them.
///
/// An *undo* of a destructive op is deliberately not snapshot-worthy — the
/// pre-op snapshot already covers that state (undo closures use plain
/// writes, never this path).
extension LibraryStore {
    public var snapshotsDirectoryURL: URL {
        folderURL.appendingPathComponent(Self.snapshotsDirectoryName)
    }

    /// Snapshot the current database state in its own barrier access.
    /// Prefer `performDestructive(reason:_:)`, which couples the snapshot to
    /// the mutation atomically.
    @discardableResult
    public func snapshotBeforeDestructive(reason: String) throws -> URL {
        try dbPool.writeWithoutTransaction { db in
            try runSnapshot(db, reason: reason)
        }
    }

    /// Snapshot, then run `mutation` inside a transaction — one barrier
    /// access, so the snapshot is always the exact pre-change state.
    ///
    /// This is HALF of the destructive-op invariant. mac/CLAUDE.md: every
    /// destructive operation takes a DB snapshot AND registers undo with a
    /// named action — both, always. This helper provides the snapshot;
    /// every call site (N5 boundary ops onward) must pair it with
    /// `registerUndo(actionName:inverse:reapply:)`.
    @discardableResult
    public func performDestructive<T>(
        reason: String,
        _ mutation: (Database) throws -> T
    ) throws -> T {
        try dbPool.writeWithoutTransaction { db in
            try runSnapshot(db, reason: reason)
            var result: T?
            try db.inTransaction {
                result = try mutation(db)
                return .commit
            }
            return result!
        }
    }

    /// Lists snapshots newest-first (filenames sort chronologically by
    /// construction).
    public func listSnapshots() -> [URL] {
        let contents = (try? FileManager.default.contentsOfDirectory(
            at: snapshotsDirectoryURL,
            includingPropertiesForKeys: nil
        )) ?? []
        return contents
            .filter { $0.pathExtension == "db" }
            .sorted { $0.lastPathComponent > $1.lastPathComponent }
    }

    // MARK: - Internals

    @discardableResult
    func runSnapshot(_ db: Database, reason: String) throws -> URL {
        let directory = snapshotsDirectoryURL
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)

        let url = directory.appendingPathComponent(
            Self.snapshotFilename(reason: reason, at: now())
        )
        try writeSnapshot(db, to: url)
        pruneSnapshots(in: directory, limit: Self.snapshotLimit)
        return url
    }

    func writeSnapshot(_ db: Database, to url: URL) throws {
        // Recorded BEFORE the VACUUM so the failure path can distinguish a
        // genuinely partial file (ours — remove it) from a pre-existing
        // snapshot that caused a filename collision (never destroy it:
        // snapshots are the crash-surviving belt; cold-review finding 1).
        let existedBefore = FileManager.default.fileExists(atPath: url.path)
        do {
            try db.execute(sql: "VACUUM INTO ?", arguments: [url.path])
        } catch {
            if !existedBefore {
                try? FileManager.default.removeItem(at: url)
            }
            throw error
        }
    }

    /// `<ISO>-<ms>-<token4>__<reason>.db`, UTC — the reference filename
    /// shape. The 4-hex token defends against same-millisecond collisions
    /// (the reference hashed file content for the same purpose; with
    /// `VACUUM INTO` the content isn't knowable before the copy, so this is
    /// a collision token, not a content hash — see the N1 PROVISIONAL note).
    static func snapshotFilename(reason: String, at date: Date) -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        let c = calendar.dateComponents(
            [.year, .month, .day, .hour, .minute, .second, .nanosecond], from: date
        )
        let iso = String(
            format: "%04d-%02d-%02dT%02d-%02d-%02d",
            c.year!, c.month!, c.day!, c.hour!, c.minute!, c.second!
        )
        let ms = String(format: "%03d", (c.nanosecond ?? 0) / 1_000_000)
        let token = String(UUID().uuidString.replacing("-", with: "").prefix(4)).lowercased()
        return "\(iso)-\(ms)-\(token)__\(safeLabel(reason)).db"
    }

    /// Filesystem-safe label: runs of anything outside `[a-zA-Z0-9._-]`
    /// collapse to a single hyphen; an empty result becomes "snapshot".
    static func safeLabel(_ reason: String) -> String {
        var out = ""
        var previousWasReplaced = false
        for scalar in reason.trimmingCharacters(in: .whitespacesAndNewlines).unicodeScalars {
            let isSafe = ("a"..."z").contains(scalar) || ("A"..."Z").contains(scalar)
                || ("0"..."9").contains(scalar) || scalar == "." || scalar == "_" || scalar == "-"
            if isSafe {
                out.unicodeScalars.append(scalar)
                previousWasReplaced = false
            } else if !previousWasReplaced {
                out.append("-")
                previousWasReplaced = true
            }
        }
        return out.isEmpty ? "snapshot" : out
    }

    private func pruneSnapshots(in directory: URL, limit: Int) {
        let sortedOldestFirst = ((try? FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: nil
        )) ?? [])
            .filter { $0.pathExtension == "db" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
        let excess = sortedOldestFirst.count - limit
        guard excess > 0 else { return }
        for url in sortedOldestFirst.prefix(excess) {
            try? FileManager.default.removeItem(at: url)
        }
    }
}
