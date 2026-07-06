/// Core domain entities — a field-for-field port of the reference `models.py`,
/// per the documented data model in `clipfarm-spec.md`.
///
/// Invariants carried here (see mac/CLAUDE.md → Invariants):
/// - All IDs are strings; clip IDs are opaque after creation (the encoded
///   `source__start__end` form is human-readable at birth only — see
///   `ClipID` in Identifiers.swift).
/// - `tracks` stays `nil` until phase N18. Readers tolerate a populated
///   value; v0/v1 writers never produce one.
/// - Timestamps stay ISO-8601 strings at rest (backup-format parity with the
///   documented JSON shape; `Date` conversion is a presentation concern).
/// - Codable conformance uses the documented snake_case keys and decodes
///   with defaults for absent keys — the substrate for the N13 backup
///   format and the test-only legacy-fixture loader.

// MARK: - Enumerated vocabularies

/// Soft categories — sorted, never hidden (spec: "Categorize material gently").
public enum ClipCategory: String, Codable, Equatable, Sendable, CaseIterable {
    case onScript = "on-script"
    case relatedButDifferent = "related-but-different"
    case standaloneIdea = "standalone-idea"
    case offTopic = "off-topic"
    case fragment = "fragment"
}

/// Tag levels within a project.
/// - `section`: a chapter / beat label (`parentID == nil`).
/// - `line`: a script line (`parentID` = section's id, or nil when the brief
///   doesn't group lines into sections — the v0 default).
/// - `tag`: an ad-hoc project-level label from the brief's `tags:` array
///   (`parentID == nil`; distinct from `line` so name-keyed merges can tell
///   them apart).
public enum TagKind: String, Codable, Equatable, Sendable, CaseIterable {
    case section
    case line
    case tag
}

public enum TagSource: String, Codable, Equatable, Sendable, CaseIterable {
    case ai
    case user
    case voiceAnnotation = "voice-annotation"
}

public enum PremadeBucket: String, Codable, Equatable, Sendable, CaseIterable {
    case best
    case diagnostic
}

public enum AttemptSource: String, Codable, Equatable, Sendable, CaseIterable {
    case aiPremade = "ai-premade"
    case handBuilt = "hand-built"
    case fork
}

// MARK: - Per-clip media composition hooks (reserved; populated at N18)

public struct AudioOverride: Equatable, Sendable {
    public var filePath: String
    public var startOffsetSec: Double

    public init(filePath: String, startOffsetSec: Double = 0.0) {
        self.filePath = filePath
        self.startOffsetSec = startOffsetSec
    }
}

public struct VideoOverride: Equatable, Sendable {
    public var sourceID: String
    public var startSec: Double
    public var endSec: Double

    public init(sourceID: String, startSec: Double, endSec: Double) {
        self.sourceID = sourceID
        self.startSec = startSec
        self.endSec = endSec
    }
}

public struct Overlay: Equatable, Sendable {
    public var startSec: Double
    public var endSec: Double
    public var type: String
    public var color: String

    public init(startSec: Double, endSec: Double, type: String = "blackout", color: String = "#000000") {
        self.startSec = startSec
        self.endSec = endSec
        self.type = type
        self.color = color
    }
}

/// Reserved schema hook for per-clip media composition (N18). Writers MUST
/// leave `Clip.tracks` as `nil` until then; readers treat a populated value
/// the same as `nil` (play the source file's audio and video unchanged).
public struct TracksOverride: Equatable, Sendable {
    public var audioOverride: AudioOverride?
    public var videoOverride: VideoOverride?
    public var overlays: [Overlay]

    public init(
        audioOverride: AudioOverride? = nil,
        videoOverride: VideoOverride? = nil,
        overlays: [Overlay] = []
    ) {
        self.audioOverride = audioOverride
        self.videoOverride = videoOverride
        self.overlays = overlays
    }
}

// MARK: - Core entities

public struct Source: Equatable, Sendable {
    public var filename: String
    public var path: String
    public var durationSec: Double?
    public var fps: Double?
    public var transcriptPath: String?
    public var addedAt: String
    public var unavailable: Bool
    // Native-schema additions (plan §2.3; populated by the N3 probe).
    public var isHDR: Bool?
    public var naturalWidth: Int?
    public var naturalHeight: Int?

    public init(
        filename: String,
        path: String,
        durationSec: Double? = nil,
        fps: Double? = nil,
        transcriptPath: String? = nil,
        addedAt: String,
        unavailable: Bool = false,
        isHDR: Bool? = nil,
        naturalWidth: Int? = nil,
        naturalHeight: Int? = nil
    ) {
        self.filename = filename
        self.path = path
        self.durationSec = durationSec
        self.fps = fps
        self.transcriptPath = transcriptPath
        self.addedAt = addedAt
        self.unavailable = unavailable
        self.isHDR = isHDR
        self.naturalWidth = naturalWidth
        self.naturalHeight = naturalHeight
    }
}

public struct Clip: Equatable, Sendable {
    public var sourceID: String
    public var startSec: Double
    public var endSec: Double
    public var transcriptText: String
    public var derivedFromClipID: String?
    /// Set by any hand boundary-correction; re-apply-segmentation skips
    /// clips carrying it (D18). Native-schema addition.
    public var boundaryEdited: Bool
    public var tracks: TracksOverride?
    public var createdAt: String

    public init(
        sourceID: String,
        startSec: Double,
        endSec: Double,
        transcriptText: String = "",
        derivedFromClipID: String? = nil,
        boundaryEdited: Bool = false,
        tracks: TracksOverride? = nil,
        createdAt: String
    ) {
        self.sourceID = sourceID
        self.startSec = startSec
        self.endSec = endSec
        self.transcriptText = transcriptText
        self.derivedFromClipID = derivedFromClipID
        self.boundaryEdited = boundaryEdited
        self.tracks = tracks
        self.createdAt = createdAt
    }
}

public struct ProjectTag: Equatable, Sendable {
    public var kind: TagKind
    public var name: String
    public var parentID: String?
    public var orderIdx: Int

    public init(kind: TagKind, name: String, parentID: String? = nil, orderIdx: Int = 0) {
        self.kind = kind
        self.name = name
        self.parentID = parentID
        self.orderIdx = orderIdx
    }
}

/// The script as the user wrote it in the brief. Each line becomes a
/// `ProjectTag(kind: .line)` in the project's tag set at parse time; this is
/// the read-back view. The section→line hierarchy lives in the tag set.
public struct Script: Equatable, Sendable {
    public var lines: [String]

    public init(lines: [String] = []) {
        self.lines = lines
    }
}

public struct Project: Equatable, Sendable {
    public var name: String
    public var briefMD: String
    /// Brief-less projects (rare; no UI path creates one) have `nil`.
    public var script: Script?
    public var tags: [String: ProjectTag]
    public var createdAt: String

    public init(
        name: String,
        briefMD: String = "",
        script: Script? = nil,
        tags: [String: ProjectTag] = [:],
        createdAt: String
    ) {
        self.name = name
        self.briefMD = briefMD
        self.script = script
        self.tags = tags
        self.createdAt = createdAt
    }
}

/// Many-to-many bridge between clips and projects. A clip can carry one
/// entry per project with its own `(section, line, category)` triple — the
/// multi-project tagging engine. Uniqueness on
/// `(clipID, projectID, projectTagID, category)` is enforced as a domain
/// rule (see ClipFarmState) with a NULL-proof DB index as the backstop.
public struct ClipProjectTag: Equatable, Sendable {
    public var clipID: String
    public var projectID: String
    public var projectTagID: String?
    public var category: ClipCategory
    public var confidence: Double
    public var source: TagSource
    public var stale: Bool
    public var notes: String

    public init(
        clipID: String,
        projectID: String,
        projectTagID: String? = nil,
        category: ClipCategory,
        confidence: Double = 1.0,
        source: TagSource = .user,
        stale: Bool = false,
        notes: String = ""
    ) {
        self.clipID = clipID
        self.projectID = projectID
        self.projectTagID = projectTagID
        self.category = category
        self.confidence = confidence
        self.source = source
        self.stale = stale
        self.notes = notes
    }
}

public struct AttemptClip: Equatable, Sendable {
    public var clipID: String
    /// Positive shrinks, negative extends. Never mutates the base clip.
    public var trimStartOffset: Double
    public var trimEndOffset: Double
    /// When set, the resolver drops any inter-word gap strictly greater than
    /// this value between sub-ranges. Per-attempt-clip; never mutates the base.
    public var internalPauseMaxSec: Double?
    public var notes: String

    public init(
        clipID: String,
        trimStartOffset: Double = 0.0,
        trimEndOffset: Double = 0.0,
        internalPauseMaxSec: Double? = nil,
        notes: String = ""
    ) {
        self.clipID = clipID
        self.trimStartOffset = trimStartOffset
        self.trimEndOffset = trimEndOffset
        self.internalPauseMaxSec = internalPauseMaxSec
        self.notes = notes
    }
}

public struct Attempt: Equatable, Sendable {
    public var projectID: String
    public var name: String
    public var parentAttemptID: String?
    public var source: AttemptSource
    /// Drives the two-bucket premade UI. Hand-built attempts and forks: `nil`.
    public var premadeBucket: PremadeBucket?
    /// Derived cache — fraction of runtime sourced from one contiguous span
    /// in one source. Recomputed on every clip-list write; readers may
    /// recompute defensively.
    public var continuityScore: Double?
    public var clips: [AttemptClip]
    /// Set by boundary correction when a referenced clip is split/deleted.
    public var needsReview: Bool
    public var createdAt: String

    public init(
        projectID: String,
        name: String,
        parentAttemptID: String? = nil,
        source: AttemptSource = .handBuilt,
        premadeBucket: PremadeBucket? = nil,
        continuityScore: Double? = nil,
        clips: [AttemptClip] = [],
        needsReview: Bool = false,
        createdAt: String
    ) {
        self.projectID = projectID
        self.name = name
        self.parentAttemptID = parentAttemptID
        self.source = source
        self.premadeBucket = premadeBucket
        self.continuityScore = continuityScore
        self.clips = clips
        self.needsReview = needsReview
        self.createdAt = createdAt
    }
}

public struct VoiceAnnotation: Equatable, Sendable {
    public var sourceID: String
    public var timestampSec: Double
    public var text: String
    public var resolvedClipID: String?
    public var targetProjectID: String?
    public var targetTagID: String?

    public init(
        sourceID: String,
        timestampSec: Double,
        text: String,
        resolvedClipID: String? = nil,
        targetProjectID: String? = nil,
        targetTagID: String? = nil
    ) {
        self.sourceID = sourceID
        self.timestampSec = timestampSec
        self.text = text
        self.resolvedClipID = resolvedClipID
        self.targetProjectID = targetProjectID
        self.targetTagID = targetTagID
    }
}
