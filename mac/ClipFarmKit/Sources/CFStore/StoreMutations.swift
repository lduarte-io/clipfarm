import CFDomain
import Foundation
import GRDB

/// N1 mutation surface. Every domain-data mutation registers an inverse
/// with the injected `UndoManager` under a named action (mac/CLAUDE.md:
/// register→undo→redo tested per mutation). Later phases add their own ops
/// (boundary correction at N5, attempts at N9/N10) on the same pattern.
///
/// Isolation: the macOS 26 SDK marks `NSUndoManager` `NS_SWIFT_UI_ACTOR`
/// (@MainActor — class and handler closures), so every undo-registering
/// mutation is explicitly `@MainActor`. This is method-level isolation, NOT
/// a target default flip (forbidden by mac/CLAUDE.md); reads, snapshots,
/// and settings stay nonisolated. Architecturally consistent with §2.7:
/// user-initiated mutations arrive from the MainActor AppStore anyway.
extension LibraryStore {
    // MARK: - Undo plumbing

    /// Registers `inverse` as the undo of the operation that just ran.
    /// When invoked, the inverse re-registers `reapply` — the symmetric
    /// flip gives a full undo/redo chain from one call site.
    ///
    /// Undo closures cannot throw; a failed replay is a programmer error in
    /// an inverse (they operate on state the paired operation just proved
    /// writable), surfaced via `assertionFailure` in debug.
    @MainActor
    func registerUndo(
        actionName: String,
        inverse: @escaping (LibraryStore) throws -> Void,
        reapply: @escaping (LibraryStore) throws -> Void
    ) {
        guard let undoManager else { return }
        undoManager.registerUndo(withTarget: self) { store in
            do {
                try inverse(store)
            } catch {
                assertionFailure("undo replay failed for '\(actionName)': \(error)")
            }
            store.registerUndo(actionName: actionName, inverse: reapply, reapply: inverse)
        }
        undoManager.setActionName(actionName)
    }

    // MARK: - Mutations

    /// Inserts a source. When `explicitID` is nil, allocates the next
    /// monotonic numeric ID over ALL existing source keys (freed slots are
    /// never reused). Undoable ("Add Source").
    @MainActor
    @discardableResult
    public func addSource(_ source: Source, id explicitID: String? = nil) throws -> String {
        // Allocation happens INSIDE the write transaction (cold-review
        // finding 3): check-then-act must never straddle two accesses, so a
        // future nonisolated bulk writer can't race the allocator.
        let id = try dbPool.write { db -> String in
            let id = try explicitID ?? nextNumericID(
                over: String.fetchAll(db, sql: "SELECT id FROM sources")
            )
            try SourceRecord(id: id, source: source).insert(db)
            return id
        }
        registerUndo(
            actionName: "Add Source",
            inverse: { store in
                try store.dbPool.write { db in
                    _ = try SourceRecord.deleteOne(db, key: id)
                }
            },
            reapply: { store in
                try store.dbPool.write { db in
                    try SourceRecord(id: id, source: source).insert(db)
                }
            }
        )
        return id
    }

    /// Inserts clips in bulk (the ingest shape: one segmentation pass, many
    /// clips, one undoable action "Add Clips"). Clip IDs are caller-encoded
    /// via `ClipID.make` at creation and opaque afterward.
    @MainActor
    public func addClips(_ newClips: [(id: String, clip: Clip)]) throws {
        guard !newClips.isEmpty else { return }
        let records = try newClips.map { try ClipRecord(id: $0.id, clip: $0.clip) }
        try dbPool.write { db in
            for record in records {
                if try ClipRecord.exists(db, key: record.id) {
                    throw LibraryStoreError.duplicateClipID(record.id)
                }
                try record.insert(db)
            }
        }
        let ids = newClips.map(\.id)
        registerUndo(
            actionName: newClips.count == 1 ? "Add Clip" : "Add Clips",
            inverse: { store in
                try store.dbPool.write { db in
                    _ = try ClipRecord.deleteAll(db, keys: ids)
                }
            },
            reapply: { store in
                try store.dbPool.write { db in
                    for record in records {
                        try record.insert(db)
                    }
                }
            }
        )
    }

    /// Inserts one clip-project-tag row. Uniqueness on
    /// `(clip_id, project_id, project_tag_id, category)` is enforced here as
    /// the domain rule (nil tag ID is a value, not a bypass); the NULL-proof
    /// unique index is the backstop. Undoable ("Tag Clip").
    @MainActor
    public func addClipProjectTag(_ tag: ClipProjectTag) throws {
        // Validate-then-insert in ONE transaction (cold-review finding 3):
        // otherwise a future nonisolated writer could interleave, demoting
        // the typed domain error to the index backstop's DatabaseError.
        try dbPool.write { db in
            let existing = try ClipProjectTagRecord.fetchAll(db).map(\.clipProjectTag)
            try validateClipProjectTagUniqueness(existing + [tag])
            try ClipProjectTagRecord(tag).insert(db)
        }
        registerUndo(
            actionName: "Tag Clip",
            inverse: { store in
                try store.dbPool.write { db in
                    try Self.deleteClipProjectTagRow(db, matching: tag)
                }
            },
            reapply: { store in
                try store.dbPool.write { db in
                    try ClipProjectTagRecord(tag).insert(db)
                }
            }
        )
    }

    static func deleteClipProjectTagRow(_ db: Database, matching tag: ClipProjectTag) throws {
        try db.execute(
            sql: """
                DELETE FROM clip_project_tags
                WHERE clip_id = ? AND project_id = ?
                  AND project_tag_id IS ? AND category = ?
                """,
            arguments: [tag.clipID, tag.projectID, tag.projectTagID, tag.category.rawValue]
        )
    }

    /// Replaces the library's *domain content* with `state` in one
    /// transaction — the fixture/restore primitive (and the eventual N13
    /// backup-restore substrate). The `settings` and `meta` tables are
    /// deliberately untouched: neither is part of the documented JSON
    /// shape; whether settings travel with a backup is an N13 decision.
    /// Deliberately NOT undo-registered: a whole-library replace clears the
    /// undo stack by design (stale inverses must never fire against
    /// replaced state).
    @MainActor
    public func importState(_ state: ClipFarmState) throws {
        try state.validate()
        undoManager?.removeAllActions()
        try dbPool.write { db in
            // FK-safe delete order (children before parents).
            try db.execute(sql: """
                DELETE FROM clip_project_tags;
                DELETE FROM attempts;
                DELETE FROM project_tags;
                DELETE FROM voice_annotations;
                DELETE FROM clips;
                DELETE FROM projects;
                DELETE FROM sources;
                """)
            for (id, source) in state.sources.sorted(by: { $0.key < $1.key }) {
                try SourceRecord(id: id, source: source).insert(db)
            }
            for (id, clip) in state.clips.sorted(by: { $0.key < $1.key }) {
                try ClipRecord(id: id, clip: clip).insert(db)
            }
            for (id, project) in state.projects.sorted(by: { $0.key < $1.key }) {
                try ProjectRecord(id: id, project: project).insert(db)
                for (tagID, tag) in project.tags.sorted(by: { $0.key < $1.key }) {
                    try ProjectTagRecord(id: tagID, projectID: id, tag: tag).insert(db)
                }
            }
            for tag in state.clipProjectTags {
                try ClipProjectTagRecord(tag).insert(db)
            }
            for (id, attempt) in state.attempts.sorted(by: { $0.key < $1.key }) {
                try AttemptRecord(id: id, attempt: attempt).insert(db)
                for (position, attemptClip) in attempt.clips.enumerated() {
                    try AttemptClipRecord(attemptID: id, position: position, attemptClip: attemptClip)
                        .insert(db)
                }
            }
            for annotation in state.voiceAnnotations {
                try VoiceAnnotationRecord(annotation).insert(db)
            }
        }
    }
}
