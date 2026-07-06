import CFDomain
import Foundation
import GRDB

/// The per-source "Re-apply segmentation settings" action (D18 / spec
/// amendment 7): recompute auto-detected clip boundaries under the CURRENT
/// per-library settings.
///
/// Semantics (N3 PROVISIONAL 3 — ID-preserving diff):
/// - clips carrying `boundary_edited` are **never touched** (hand
///   corrections survive every re-apply — the D18 promise);
/// - recomputed ranges whose encoded ID already exists keep their existing
///   row untouched (tags and `created_at` survive);
/// - auto-detected clips whose ID is not in the recomputed set are deleted
///   (their tag rows dropped explicitly — the spec's clip-delete rule — and
///   attempts referencing them flagged `needs_review`, the spec's
///   delete-propagation rule applied forward of N5);
/// - recomputed ranges not present are inserted.
///
/// Destructive-op invariant: DB snapshot (`re-apply-segmentation`) AND a
/// named undo registration ("Re-apply Segmentation") — both, always. A
/// true no-op (nothing to delete or insert) takes neither.
public struct ReapplySegmentationResult: Equatable, Sendable {
    public var clipsRemoved: Int
    public var clipsAdded: Int
    /// Recomputed ranges that already existed as rows (kept untouched).
    public var clipsKept: Int
    /// Hand-corrected clips skipped per the `boundary_edited` rule.
    public var skippedBoundaryEdited: Int

    public var changed: Bool { clipsRemoved > 0 || clipsAdded > 0 }
}

public enum ReapplySegmentationError: Error, Equatable {
    case unknownSource(String)
    case noTranscript(sourceID: String)
    case transcriptUnusable(sourceID: String, detail: String)
}

extension LibraryStore {
    /// `transcript` is injectable for tests; by default the source's sidecar
    /// is loaded from `transcript_path`.
    @MainActor
    @discardableResult
    public func reapplySegmentation(
        forSourceID sourceID: String,
        transcript injected: WhisperTranscript? = nil
    ) throws -> ReapplySegmentationResult {
        guard let source = try source(id: sourceID) else {
            throw ReapplySegmentationError.unknownSource(sourceID)
        }
        let transcript: WhisperTranscript
        if let injected {
            transcript = injected
        } else {
            guard let transcriptPath = source.transcriptPath else {
                throw ReapplySegmentationError.noTranscript(sourceID: sourceID)
            }
            switch Sidecar.load(at: URL(fileURLWithPath: transcriptPath)) {
            case .ok(let loaded):
                transcript = loaded
            case .rejected(let rejection):
                throw ReapplySegmentationError.transcriptUnusable(
                    sourceID: sourceID,
                    detail: "\(rejection.reason.rawValue): \(rejection.detail)"
                )
            }
        }

        let settings = try librarySettings()
        let stem = (source.filename as NSString).deletingPathExtension
        let ranges = try Segmentation.segment(
            words: transcript.allWords,
            gapThresholdSec: settings.silenceThresholdSec,
            tailPolicy: settings.tailPolicy,
            tailPaddingSec: settings.tailPaddingSec,
            sourceDurationSec: source.durationSec
        )

        let existing = try dbPool.read { db in
            try ClipRecord.fetchAll(
                db, sql: "SELECT * FROM clips WHERE source_id = ?", arguments: [sourceID])
        }
        let existingIDs = Set(existing.map(\.id))
        let recomputed: [(id: String, range: ClipRange)] = ranges.map {
            (ClipID.make(sourceStem: stem, start: $0.startSec, end: $0.endSec), $0)
        }
        let recomputedIDs = Set(recomputed.map(\.id))

        let deletions = existing.filter { !$0.boundaryEdited && !recomputedIDs.contains($0.id) }
        let stamp = iso8601(now())
        let insertions: [ClipRecord] = try recomputed
            .filter { !existingIDs.contains($0.id) }
            .map { entry in
                try ClipRecord(
                    id: entry.id,
                    clip: Clip(
                        sourceID: sourceID,
                        startSec: entry.range.startSec,
                        endSec: entry.range.endSec,
                        transcriptText: transcript.transcriptText(
                            from: entry.range.startSec, to: entry.range.endSec),
                        createdAt: stamp
                    )
                )
            }

        let result = ReapplySegmentationResult(
            clipsRemoved: deletions.count,
            clipsAdded: insertions.count,
            clipsKept: recomputedIDs.intersection(existingIDs).count,
            skippedBoundaryEdited: existing.count(where: \.boundaryEdited)
        )
        guard result.changed else { return result }

        // Capture everything the undo needs BEFORE mutating. A pure-insertion
        // diff (every existing clip boundary_edited or kept) deletes nothing,
        // so there is nothing to capture — and an IN () query with zero bound
        // arguments would throw (cold-review finding 1).
        let deletedIDs = deletions.map(\.id)
        let (deletedTagRows, flaggedAttempts): ([ClipProjectTagRecord], [(id: String, needsReviewBefore: Bool)])
        if deletedIDs.isEmpty {
            (deletedTagRows, flaggedAttempts) = ([], [])
        } else {
            (deletedTagRows, flaggedAttempts) = try dbPool.read { db in
                let tags = try ClipProjectTagRecord.fetchAll(
                    db,
                    sql: "SELECT * FROM clip_project_tags WHERE clip_id IN "
                        + Self.placeholderList(deletedIDs.count) + " ORDER BY rowid",
                    arguments: StatementArguments(deletedIDs)
                )
                // Attempts referencing a deleted clip get needs_review (the
                // spec's delete-propagation rule); capture before-values so
                // undo restores them exactly.
                let attempts = try Row.fetchAll(
                    db,
                    sql: """
                        SELECT DISTINCT a.id AS id, a.needs_review AS needs_review
                        FROM attempts a JOIN attempt_clips ac ON ac.attempt_id = a.id
                        WHERE ac.clip_id IN \(Self.placeholderList(deletedIDs.count))
                        """,
                    arguments: StatementArguments(deletedIDs)
                ).map { (id: $0["id"] as String, needsReviewBefore: $0["needs_review"] as Bool) }
                return (tags, attempts)
            }
        }

        let apply: (Database) throws -> Void = { db in
            try Self.deleteClipsAndTags(db, clipIDs: deletedIDs)
            for record in insertions { try record.insert(db) }
            for flagged in flaggedAttempts {
                try db.execute(
                    sql: "UPDATE attempts SET needs_review = 1 WHERE id = ?",
                    arguments: [flagged.id])
            }
        }
        let revert: (Database) throws -> Void = { db in
            _ = try ClipRecord.deleteAll(db, keys: insertions.map(\.id))
            for record in deletions { try record.insert(db) }
            for tag in deletedTagRows { try tag.insert(db) }
            for flagged in flaggedAttempts {
                try db.execute(
                    sql: "UPDATE attempts SET needs_review = ? WHERE id = ?",
                    arguments: [flagged.needsReviewBefore, flagged.id])
            }
        }

        try performDestructive(reason: "re-apply-segmentation", apply)
        registerUndo(
            actionName: "Re-apply Segmentation",
            inverse: { store in try store.dbPool.write(revert) },
            reapply: { store in try store.dbPool.write(apply) }
        )
        return result
    }

    static func deleteClipsAndTags(_ db: Database, clipIDs: [String]) throws {
        guard !clipIDs.isEmpty else { return }
        // Tag rows first (clip_project_tags.clip_id is an FK).
        try db.execute(
            sql: "DELETE FROM clip_project_tags WHERE clip_id IN " + placeholderList(clipIDs.count),
            arguments: StatementArguments(clipIDs)
        )
        _ = try ClipRecord.deleteAll(db, keys: clipIDs)
    }

    /// `(?, ?, …)` for an `IN` clause. Zero placeholders is a programmer
    /// error — every call site guards empty ID lists before building SQL
    /// (an `IN ()` with no bound arguments throws at bind time).
    static func placeholderList(_ count: Int) -> String {
        precondition(count > 0, "placeholderList requires at least one value")
        return "(" + Array(repeating: "?", count: count).joined(separator: ", ") + ")"
    }
}
