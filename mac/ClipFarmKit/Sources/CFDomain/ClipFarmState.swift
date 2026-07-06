/// The whole-library value snapshot — mirrors the documented `clipfarm.json`
/// shape (now the backup/interchange format; at rest the same entities live
/// in SQLite via CFStore).
///
/// Used by: fixture builders, round-trip tests, the future backup
/// exporter/restorer (N13), and pure domain functions that need cross-entity
/// context (resolver, continuity).

public struct ClipFarmState: Equatable, Sendable {
    /// The current state-shape version. One versioning stream with the
    /// database schema (the backup JSON carries the schema version so
    /// restores migrate — plan §2.3); CFStore's `LibrarySchema` mirrors it.
    /// History: 1 = web v0 shape; 2 = N3 (`sources.original_path`).
    public static let currentVersion = 2

    public var version: Int
    public var sources: [String: Source]
    public var clips: [String: Clip]
    public var projects: [String: Project]
    public var clipProjectTags: [ClipProjectTag]
    public var attempts: [String: Attempt]
    public var voiceAnnotations: [VoiceAnnotation]

    public init(
        version: Int = ClipFarmState.currentVersion,
        sources: [String: Source] = [:],
        clips: [String: Clip] = [:],
        projects: [String: Project] = [:],
        clipProjectTags: [ClipProjectTag] = [],
        attempts: [String: Attempt] = [:],
        voiceAnnotations: [VoiceAnnotation] = []
    ) {
        self.version = version
        self.sources = sources
        self.clips = clips
        self.projects = projects
        self.clipProjectTags = clipProjectTags
        self.attempts = attempts
        self.voiceAnnotations = voiceAnnotations
    }
}

extension ClipFarmState: Codable {
    enum CodingKeys: String, CodingKey {
        case version
        case sources
        case clips
        case projects
        case clipProjectTags = "clip_project_tags"
        case attempts
        case voiceAnnotations = "voice_annotations"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            version: try c.decodeIfPresent(Int.self, forKey: .version) ?? 1,
            sources: try c.decodeIfPresent([String: Source].self, forKey: .sources) ?? [:],
            clips: try c.decodeIfPresent([String: Clip].self, forKey: .clips) ?? [:],
            projects: try c.decodeIfPresent([String: Project].self, forKey: .projects) ?? [:],
            clipProjectTags: try c.decodeIfPresent([ClipProjectTag].self, forKey: .clipProjectTags) ?? [],
            attempts: try c.decodeIfPresent([String: Attempt].self, forKey: .attempts) ?? [:],
            voiceAnnotations: try c.decodeIfPresent([VoiceAnnotation].self, forKey: .voiceAnnotations) ?? []
        )
    }
}

// MARK: - Clip-project-tag uniqueness (the domain rule; finding 10)

/// Uniqueness key for `ClipProjectTag` rows:
/// `(clip_id, project_id, project_tag_id, category)`. `nil` projectTagID is
/// a value, not a bypass — two bucket-category rows for the same
/// clip+project+category are duplicates. Same tag with a different category
/// is NOT a duplicate (a clip can be on-script AND standalone-idea for the
/// same line).
public struct ClipProjectTagKey: Hashable, Sendable {
    public let clipID: String
    public let projectID: String
    public let projectTagID: String?
    public let category: ClipCategory

    public init(_ tag: ClipProjectTag) {
        self.clipID = tag.clipID
        self.projectID = tag.projectID
        self.projectTagID = tag.projectTagID
        self.category = tag.category
    }
}

public enum ClipProjectTagUniquenessError: Error, Equatable {
    case duplicate(ClipProjectTagKey)
}

/// Throws on the first duplicate uniqueness key. Domain validation is the
/// enforcer; the NULL-proof unique index in the DB schema is the backstop
/// (SQLite unique indexes treat bare NULLs as distinct — hence COALESCE in
/// the index and this rule up front).
public func validateClipProjectTagUniqueness(_ tags: [ClipProjectTag]) throws {
    var seen = Set<ClipProjectTagKey>()
    for tag in tags {
        let key = ClipProjectTagKey(tag)
        guard seen.insert(key).inserted else {
            throw ClipProjectTagUniquenessError.duplicate(key)
        }
    }
}

public enum StateValidationError: Error, Equatable {
    /// The reference model required `name: min_length=1`; empty-named
    /// projects are rejected at the same seams (import/load + mutations).
    case emptyProjectName(projectID: String)
}

extension ClipFarmState {
    /// Validates cross-entity domain rules. Called by CFStore on import and
    /// by mutation paths before rows land. (Struct construction itself is
    /// unchecked — this function is the enforcement seam, plus N6's
    /// create/update-project ops.)
    public func validate() throws {
        try validateClipProjectTagUniqueness(clipProjectTags)
        for (id, project) in projects where project.name.isEmpty {
            throw StateValidationError.emptyProjectName(projectID: id)
        }
    }
}
