import CFDomain
import GRDB

/// N1 read surface. `fetchState()` is the whole-library value snapshot —
/// the backup/fixture/round-trip primitive, fine at personal-library scale.
/// Per-view incremental reads arrive with GRDB `ValueObservation` at N4+.
extension LibraryStore {
    public func fetchState() throws -> ClipFarmState {
        try dbPool.read { db in
            let version = try Int.fetchOne(
                db, sql: "SELECT CAST(value AS INTEGER) FROM meta WHERE key = 'schema_version'"
            ) ?? LibrarySchema.schemaVersion

            var sources: [String: Source] = [:]
            for record in try SourceRecord.fetchAll(db) {
                sources[record.id] = record.source
            }

            var clips: [String: Clip] = [:]
            for record in try ClipRecord.fetchAll(db) {
                clips[record.id] = try record.clip()
            }

            var tagsByProject: [String: [String: ProjectTag]] = [:]
            for record in try ProjectTagRecord.fetchAll(db) {
                tagsByProject[record.projectID, default: [:]][record.id] = record.projectTag
            }
            var projects: [String: Project] = [:]
            for record in try ProjectRecord.fetchAll(db) {
                projects[record.id] = try record.project(tags: tagsByProject[record.id] ?? [:])
            }

            let clipProjectTags = try ClipProjectTagRecord
                .fetchAll(db, sql: "SELECT * FROM clip_project_tags ORDER BY rowid")
                .map(\.clipProjectTag)

            var attempts: [String: Attempt] = [:]
            for record in try AttemptRecord.fetchAll(db) {
                let clips = try AttemptClipRecord
                    .fetchAll(
                        db,
                        sql: "SELECT * FROM attempt_clips WHERE attempt_id = ? ORDER BY position",
                        arguments: [record.id]
                    )
                    .map(\.attemptClip)
                attempts[record.id] = record.attempt(clips: clips)
            }

            let voiceAnnotations = try VoiceAnnotationRecord
                .fetchAll(db, sql: "SELECT * FROM voice_annotations ORDER BY rowid")
                .map(\.voiceAnnotation)

            return ClipFarmState(
                version: version,
                sources: sources,
                clips: clips,
                projects: projects,
                clipProjectTags: clipProjectTags,
                attempts: attempts,
                voiceAnnotations: voiceAnnotations
            )
        }
    }

    public func source(id: String) throws -> Source? {
        try dbPool.read { db in
            try SourceRecord.fetchOne(db, key: id)?.source
        }
    }

    public func clip(id: String) throws -> Clip? {
        try dbPool.read { db in
            try ClipRecord.fetchOne(db, key: id)?.clip()
        }
    }
}
