import CFDomain
import Foundation
import GRDB

/// GRDB record adapters — the mapping between CFDomain value types (which
/// stay GRDB-free) and the §2.3 tables. Column names are the CodingKeys'
/// snake_case raw values.
///
/// `clips.tracks` and `projects.script_lines` persist as JSON text columns;
/// the mapping (de)serializes them here so nothing outside CFStore knows.

private let jsonEncoder = JSONEncoder()
private let jsonDecoder = JSONDecoder()

struct SourceRecord: Codable, FetchableRecord, PersistableRecord {
    static let databaseTableName = "sources"

    enum CodingKeys: String, CodingKey {
        case id, filename, path, fps, unavailable
        case durationSec = "duration_sec"
        case transcriptPath = "transcript_path"
        case addedAt = "added_at"
        case isHDR = "is_hdr"
        case naturalWidth = "natural_width"
        case naturalHeight = "natural_height"
    }

    var id: String
    var filename: String
    var path: String
    var durationSec: Double?
    var fps: Double?
    var transcriptPath: String?
    var addedAt: String
    var unavailable: Bool
    var isHDR: Bool?
    var naturalWidth: Int?
    var naturalHeight: Int?

    init(id: String, source: Source) {
        self.id = id
        self.filename = source.filename
        self.path = source.path
        self.durationSec = source.durationSec
        self.fps = source.fps
        self.transcriptPath = source.transcriptPath
        self.addedAt = source.addedAt
        self.unavailable = source.unavailable
        self.isHDR = source.isHDR
        self.naturalWidth = source.naturalWidth
        self.naturalHeight = source.naturalHeight
    }

    var source: Source {
        Source(
            filename: filename,
            path: path,
            durationSec: durationSec,
            fps: fps,
            transcriptPath: transcriptPath,
            addedAt: addedAt,
            unavailable: unavailable,
            isHDR: isHDR,
            naturalWidth: naturalWidth,
            naturalHeight: naturalHeight
        )
    }
}

struct ClipRecord: Codable, FetchableRecord, PersistableRecord {
    static let databaseTableName = "clips"

    enum CodingKeys: String, CodingKey {
        case id, tracks
        case sourceID = "source_id"
        case startSec = "start_sec"
        case endSec = "end_sec"
        case transcriptText = "transcript_text"
        case derivedFromClipID = "derived_from_clip_id"
        case boundaryEdited = "boundary_edited"
        case createdAt = "created_at"
    }

    var id: String
    var sourceID: String
    var startSec: Double
    var endSec: Double
    var transcriptText: String
    var derivedFromClipID: String?
    var boundaryEdited: Bool
    /// JSON text; NULL until N18 by the writer invariant.
    var tracks: String?
    var createdAt: String

    init(id: String, clip: Clip) throws {
        self.id = id
        self.sourceID = clip.sourceID
        self.startSec = clip.startSec
        self.endSec = clip.endSec
        self.transcriptText = clip.transcriptText
        self.derivedFromClipID = clip.derivedFromClipID
        self.boundaryEdited = clip.boundaryEdited
        self.tracks = try clip.tracks.map { String(decoding: try jsonEncoder.encode($0), as: UTF8.self) }
        self.createdAt = clip.createdAt
    }

    func clip() throws -> Clip {
        Clip(
            sourceID: sourceID,
            startSec: startSec,
            endSec: endSec,
            transcriptText: transcriptText,
            derivedFromClipID: derivedFromClipID,
            boundaryEdited: boundaryEdited,
            tracks: try tracks.map { try jsonDecoder.decode(TracksOverride.self, from: Data($0.utf8)) },
            createdAt: createdAt
        )
    }
}

struct ProjectRecord: Codable, FetchableRecord, PersistableRecord {
    static let databaseTableName = "projects"

    enum CodingKeys: String, CodingKey {
        case id, name
        case briefMD = "brief_md"
        case scriptLines = "script_lines"
        case createdAt = "created_at"
    }

    var id: String
    var name: String
    var briefMD: String
    /// JSON array of strings, or NULL for a script-less project. The
    /// Optional distinction is load-bearing: `Script(lines: [])` → `'[]'`,
    /// `nil` → NULL.
    var scriptLines: String?
    var createdAt: String

    init(id: String, project: Project) throws {
        self.id = id
        self.name = project.name
        self.briefMD = project.briefMD
        self.scriptLines = try project.script.map {
            String(decoding: try jsonEncoder.encode($0.lines), as: UTF8.self)
        }
        self.createdAt = project.createdAt
    }

    /// Rebuilds the domain project; `tags` are attached by the caller from
    /// `project_tags` rows.
    func project(tags: [String: ProjectTag]) throws -> Project {
        Project(
            name: name,
            briefMD: briefMD,
            script: try scriptLines.map { Script(lines: try jsonDecoder.decode([String].self, from: Data($0.utf8))) },
            tags: tags,
            createdAt: createdAt
        )
    }
}

struct ProjectTagRecord: Codable, FetchableRecord, PersistableRecord {
    static let databaseTableName = "project_tags"

    enum CodingKeys: String, CodingKey {
        case id, kind, name
        case projectID = "project_id"
        case parentID = "parent_id"
        case orderIdx = "order_idx"
    }

    var id: String
    var projectID: String
    var kind: TagKind
    var name: String
    var parentID: String?
    var orderIdx: Int

    init(id: String, projectID: String, tag: ProjectTag) {
        self.id = id
        self.projectID = projectID
        self.kind = tag.kind
        self.name = tag.name
        self.parentID = tag.parentID
        self.orderIdx = tag.orderIdx
    }

    var projectTag: ProjectTag {
        ProjectTag(kind: kind, name: name, parentID: parentID, orderIdx: orderIdx)
    }
}

struct ClipProjectTagRecord: Codable, FetchableRecord, PersistableRecord {
    static let databaseTableName = "clip_project_tags"

    enum CodingKeys: String, CodingKey {
        case category, confidence, source, stale, notes
        case clipID = "clip_id"
        case projectID = "project_id"
        case projectTagID = "project_tag_id"
    }

    var clipID: String
    var projectID: String
    var projectTagID: String?
    var category: ClipCategory
    var confidence: Double
    var source: TagSource
    var stale: Bool
    var notes: String

    init(_ tag: ClipProjectTag) {
        self.clipID = tag.clipID
        self.projectID = tag.projectID
        self.projectTagID = tag.projectTagID
        self.category = tag.category
        self.confidence = tag.confidence
        self.source = tag.source
        self.stale = tag.stale
        self.notes = tag.notes
    }

    var clipProjectTag: ClipProjectTag {
        ClipProjectTag(
            clipID: clipID,
            projectID: projectID,
            projectTagID: projectTagID,
            category: category,
            confidence: confidence,
            source: source,
            stale: stale,
            notes: notes
        )
    }
}

struct AttemptRecord: Codable, FetchableRecord, PersistableRecord {
    static let databaseTableName = "attempts"

    enum CodingKeys: String, CodingKey {
        case id, name, source
        case projectID = "project_id"
        case parentAttemptID = "parent_attempt_id"
        case premadeBucket = "premade_bucket"
        case continuityScore = "continuity_score"
        case needsReview = "needs_review"
        case createdAt = "created_at"
    }

    var id: String
    var projectID: String
    var name: String
    var parentAttemptID: String?
    var source: AttemptSource
    var premadeBucket: PremadeBucket?
    var continuityScore: Double?
    var needsReview: Bool
    var createdAt: String

    init(id: String, attempt: Attempt) {
        self.id = id
        self.projectID = attempt.projectID
        self.name = attempt.name
        self.parentAttemptID = attempt.parentAttemptID
        self.source = attempt.source
        self.premadeBucket = attempt.premadeBucket
        self.continuityScore = attempt.continuityScore
        self.needsReview = attempt.needsReview
        self.createdAt = attempt.createdAt
    }

    /// Rebuilds the domain attempt; ordered `clips` are attached by the
    /// caller from `attempt_clips` rows.
    func attempt(clips: [AttemptClip]) -> Attempt {
        Attempt(
            projectID: projectID,
            name: name,
            parentAttemptID: parentAttemptID,
            source: source,
            premadeBucket: premadeBucket,
            continuityScore: continuityScore,
            clips: clips,
            needsReview: needsReview,
            createdAt: createdAt
        )
    }
}

struct AttemptClipRecord: Codable, FetchableRecord, PersistableRecord {
    static let databaseTableName = "attempt_clips"

    enum CodingKeys: String, CodingKey {
        case position, notes
        case attemptID = "attempt_id"
        case clipID = "clip_id"
        case trimStartOffset = "trim_start_offset"
        case trimEndOffset = "trim_end_offset"
        case internalPauseMaxSec = "internal_pause_max_sec"
    }

    var attemptID: String
    var position: Int
    var clipID: String
    var trimStartOffset: Double
    var trimEndOffset: Double
    var internalPauseMaxSec: Double?
    var notes: String

    init(attemptID: String, position: Int, attemptClip: AttemptClip) {
        self.attemptID = attemptID
        self.position = position
        self.clipID = attemptClip.clipID
        self.trimStartOffset = attemptClip.trimStartOffset
        self.trimEndOffset = attemptClip.trimEndOffset
        self.internalPauseMaxSec = attemptClip.internalPauseMaxSec
        self.notes = attemptClip.notes
    }

    var attemptClip: AttemptClip {
        AttemptClip(
            clipID: clipID,
            trimStartOffset: trimStartOffset,
            trimEndOffset: trimEndOffset,
            internalPauseMaxSec: internalPauseMaxSec,
            notes: notes
        )
    }
}

struct VoiceAnnotationRecord: Codable, FetchableRecord, PersistableRecord {
    static let databaseTableName = "voice_annotations"

    enum CodingKeys: String, CodingKey {
        case text
        case sourceID = "source_id"
        case timestampSec = "timestamp_sec"
        case resolvedClipID = "resolved_clip_id"
        case targetProjectID = "target_project_id"
        case targetTagID = "target_tag_id"
    }

    var sourceID: String
    var timestampSec: Double
    var text: String
    var resolvedClipID: String?
    var targetProjectID: String?
    var targetTagID: String?

    init(_ annotation: VoiceAnnotation) {
        self.sourceID = annotation.sourceID
        self.timestampSec = annotation.timestampSec
        self.text = annotation.text
        self.resolvedClipID = annotation.resolvedClipID
        self.targetProjectID = annotation.targetProjectID
        self.targetTagID = annotation.targetTagID
    }

    var voiceAnnotation: VoiceAnnotation {
        VoiceAnnotation(
            sourceID: sourceID,
            timestampSec: timestampSec,
            text: text,
            resolvedClipID: resolvedClipID,
            targetProjectID: targetProjectID,
            targetTagID: targetTagID
        )
    }
}

struct SettingRecord: Codable, FetchableRecord, PersistableRecord {
    static let databaseTableName = "settings"
    var key: String
    var value: String
}
