/// Codable conformances for the domain entities, using the documented
/// snake_case JSON keys from `clipfarm-spec.md` → Data model.
///
/// Decoding applies the same defaults the reference models declared, so a
/// minimal/legacy JSON object (the test-only fixture loader's input, and
/// eventually the N13 tolerant backup-restore path) decodes without every
/// key present. Encoding is straightforward keyed encoding; the N13 backup
/// exporter owns the exact null-emission contract.

// MARK: - Tracks hooks

extension AudioOverride: Codable {
    enum CodingKeys: String, CodingKey {
        case filePath = "file_path"
        case startOffsetSec = "start_offset_sec"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            filePath: try c.decode(String.self, forKey: .filePath),
            startOffsetSec: try c.decodeIfPresent(Double.self, forKey: .startOffsetSec) ?? 0.0
        )
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(filePath, forKey: .filePath)
        try c.encode(startOffsetSec, forKey: .startOffsetSec)
    }
}

extension VideoOverride: Codable {
    enum CodingKeys: String, CodingKey {
        case sourceID = "source_id"
        case startSec = "start_sec"
        case endSec = "end_sec"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            sourceID: try c.decode(String.self, forKey: .sourceID),
            startSec: try c.decode(Double.self, forKey: .startSec),
            endSec: try c.decode(Double.self, forKey: .endSec)
        )
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(sourceID, forKey: .sourceID)
        try c.encode(startSec, forKey: .startSec)
        try c.encode(endSec, forKey: .endSec)
    }
}

extension Overlay: Codable {
    enum CodingKeys: String, CodingKey {
        case startSec = "start_sec"
        case endSec = "end_sec"
        case type
        case color
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            startSec: try c.decode(Double.self, forKey: .startSec),
            endSec: try c.decode(Double.self, forKey: .endSec),
            type: try c.decodeIfPresent(String.self, forKey: .type) ?? "blackout",
            color: try c.decodeIfPresent(String.self, forKey: .color) ?? "#000000"
        )
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(startSec, forKey: .startSec)
        try c.encode(endSec, forKey: .endSec)
        try c.encode(type, forKey: .type)
        try c.encode(color, forKey: .color)
    }
}

extension TracksOverride: Codable {
    enum CodingKeys: String, CodingKey {
        case audioOverride = "audio_override"
        case videoOverride = "video_override"
        case overlays
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            audioOverride: try c.decodeIfPresent(AudioOverride.self, forKey: .audioOverride),
            videoOverride: try c.decodeIfPresent(VideoOverride.self, forKey: .videoOverride),
            overlays: try c.decodeIfPresent([Overlay].self, forKey: .overlays) ?? []
        )
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(audioOverride, forKey: .audioOverride)
        try c.encode(videoOverride, forKey: .videoOverride)
        try c.encode(overlays, forKey: .overlays)
    }
}

// MARK: - Core entities

extension Source: Codable {
    enum CodingKeys: String, CodingKey {
        case filename
        case path
        case originalPath = "original_path"
        case durationSec = "duration_sec"
        case fps
        case transcriptPath = "transcript_path"
        case addedAt = "added_at"
        case unavailable
        case isHDR = "is_hdr"
        case naturalWidth = "natural_width"
        case naturalHeight = "natural_height"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            filename: try c.decode(String.self, forKey: .filename),
            path: try c.decode(String.self, forKey: .path),
            originalPath: try c.decodeIfPresent(String.self, forKey: .originalPath),
            durationSec: try c.decodeIfPresent(Double.self, forKey: .durationSec),
            fps: try c.decodeIfPresent(Double.self, forKey: .fps),
            transcriptPath: try c.decodeIfPresent(String.self, forKey: .transcriptPath),
            addedAt: try c.decode(String.self, forKey: .addedAt),
            unavailable: try c.decodeIfPresent(Bool.self, forKey: .unavailable) ?? false,
            isHDR: try c.decodeIfPresent(Bool.self, forKey: .isHDR),
            naturalWidth: try c.decodeIfPresent(Int.self, forKey: .naturalWidth),
            naturalHeight: try c.decodeIfPresent(Int.self, forKey: .naturalHeight)
        )
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(filename, forKey: .filename)
        try c.encode(path, forKey: .path)
        try c.encode(originalPath, forKey: .originalPath)
        try c.encode(durationSec, forKey: .durationSec)
        try c.encode(fps, forKey: .fps)
        try c.encode(transcriptPath, forKey: .transcriptPath)
        try c.encode(addedAt, forKey: .addedAt)
        try c.encode(unavailable, forKey: .unavailable)
        try c.encode(isHDR, forKey: .isHDR)
        try c.encode(naturalWidth, forKey: .naturalWidth)
        try c.encode(naturalHeight, forKey: .naturalHeight)
    }
}

extension Clip: Codable {
    enum CodingKeys: String, CodingKey {
        case sourceID = "source_id"
        case startSec = "start_sec"
        case endSec = "end_sec"
        case transcriptText = "transcript_text"
        case derivedFromClipID = "derived_from_clip_id"
        case boundaryEdited = "boundary_edited"
        case tracks
        case createdAt = "created_at"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            sourceID: try c.decode(String.self, forKey: .sourceID),
            startSec: try c.decode(Double.self, forKey: .startSec),
            endSec: try c.decode(Double.self, forKey: .endSec),
            transcriptText: try c.decodeIfPresent(String.self, forKey: .transcriptText) ?? "",
            derivedFromClipID: try c.decodeIfPresent(String.self, forKey: .derivedFromClipID),
            boundaryEdited: try c.decodeIfPresent(Bool.self, forKey: .boundaryEdited) ?? false,
            tracks: try c.decodeIfPresent(TracksOverride.self, forKey: .tracks),
            createdAt: try c.decode(String.self, forKey: .createdAt)
        )
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(sourceID, forKey: .sourceID)
        try c.encode(startSec, forKey: .startSec)
        try c.encode(endSec, forKey: .endSec)
        try c.encode(transcriptText, forKey: .transcriptText)
        try c.encode(derivedFromClipID, forKey: .derivedFromClipID)
        try c.encode(boundaryEdited, forKey: .boundaryEdited)
        try c.encode(tracks, forKey: .tracks)
        try c.encode(createdAt, forKey: .createdAt)
    }
}

extension ProjectTag: Codable {
    enum CodingKeys: String, CodingKey {
        case kind
        case name
        case parentID = "parent_id"
        case orderIdx = "order_idx"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            kind: try c.decode(TagKind.self, forKey: .kind),
            name: try c.decode(String.self, forKey: .name),
            parentID: try c.decodeIfPresent(String.self, forKey: .parentID),
            orderIdx: try c.decodeIfPresent(Int.self, forKey: .orderIdx) ?? 0
        )
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(kind, forKey: .kind)
        try c.encode(name, forKey: .name)
        try c.encode(parentID, forKey: .parentID)
        try c.encode(orderIdx, forKey: .orderIdx)
    }
}

extension Script: Codable {
    enum CodingKeys: String, CodingKey {
        case lines
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(lines: try c.decodeIfPresent([String].self, forKey: .lines) ?? [])
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(lines, forKey: .lines)
    }
}

extension Project: Codable {
    enum CodingKeys: String, CodingKey {
        case name
        case briefMD = "brief_md"
        case script
        case tags
        case createdAt = "created_at"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            name: try c.decode(String.self, forKey: .name),
            briefMD: try c.decodeIfPresent(String.self, forKey: .briefMD) ?? "",
            script: try c.decodeIfPresent(Script.self, forKey: .script),
            tags: try c.decodeIfPresent([String: ProjectTag].self, forKey: .tags) ?? [:],
            createdAt: try c.decode(String.self, forKey: .createdAt)
        )
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(name, forKey: .name)
        try c.encode(briefMD, forKey: .briefMD)
        try c.encode(script, forKey: .script)
        try c.encode(tags, forKey: .tags)
        try c.encode(createdAt, forKey: .createdAt)
    }
}

extension ClipProjectTag: Codable {
    enum CodingKeys: String, CodingKey {
        case clipID = "clip_id"
        case projectID = "project_id"
        case projectTagID = "project_tag_id"
        case category
        case confidence
        case source
        case stale
        case notes
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            clipID: try c.decode(String.self, forKey: .clipID),
            projectID: try c.decode(String.self, forKey: .projectID),
            projectTagID: try c.decodeIfPresent(String.self, forKey: .projectTagID),
            category: try c.decode(ClipCategory.self, forKey: .category),
            confidence: try c.decodeIfPresent(Double.self, forKey: .confidence) ?? 1.0,
            source: try c.decodeIfPresent(TagSource.self, forKey: .source) ?? .user,
            stale: try c.decodeIfPresent(Bool.self, forKey: .stale) ?? false,
            notes: try c.decodeIfPresent(String.self, forKey: .notes) ?? ""
        )
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(clipID, forKey: .clipID)
        try c.encode(projectID, forKey: .projectID)
        try c.encode(projectTagID, forKey: .projectTagID)
        try c.encode(category, forKey: .category)
        try c.encode(confidence, forKey: .confidence)
        try c.encode(source, forKey: .source)
        try c.encode(stale, forKey: .stale)
        try c.encode(notes, forKey: .notes)
    }
}

extension AttemptClip: Codable {
    enum CodingKeys: String, CodingKey {
        case clipID = "clip_id"
        case trimStartOffset = "trim_start_offset"
        case trimEndOffset = "trim_end_offset"
        case internalPauseMaxSec = "internal_pause_max_sec"
        case notes
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            clipID: try c.decode(String.self, forKey: .clipID),
            trimStartOffset: try c.decodeIfPresent(Double.self, forKey: .trimStartOffset) ?? 0.0,
            trimEndOffset: try c.decodeIfPresent(Double.self, forKey: .trimEndOffset) ?? 0.0,
            internalPauseMaxSec: try c.decodeIfPresent(Double.self, forKey: .internalPauseMaxSec),
            notes: try c.decodeIfPresent(String.self, forKey: .notes) ?? ""
        )
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(clipID, forKey: .clipID)
        try c.encode(trimStartOffset, forKey: .trimStartOffset)
        try c.encode(trimEndOffset, forKey: .trimEndOffset)
        try c.encode(internalPauseMaxSec, forKey: .internalPauseMaxSec)
        try c.encode(notes, forKey: .notes)
    }
}

extension Attempt: Codable {
    enum CodingKeys: String, CodingKey {
        case projectID = "project_id"
        case name
        case parentAttemptID = "parent_attempt_id"
        case source
        case premadeBucket = "premade_bucket"
        case continuityScore = "continuity_score"
        case clips
        case needsReview = "needs_review"
        case createdAt = "created_at"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            projectID: try c.decode(String.self, forKey: .projectID),
            name: try c.decode(String.self, forKey: .name),
            parentAttemptID: try c.decodeIfPresent(String.self, forKey: .parentAttemptID),
            source: try c.decodeIfPresent(AttemptSource.self, forKey: .source) ?? .handBuilt,
            premadeBucket: try c.decodeIfPresent(PremadeBucket.self, forKey: .premadeBucket),
            continuityScore: try c.decodeIfPresent(Double.self, forKey: .continuityScore),
            clips: try c.decodeIfPresent([AttemptClip].self, forKey: .clips) ?? [],
            needsReview: try c.decodeIfPresent(Bool.self, forKey: .needsReview) ?? false,
            createdAt: try c.decode(String.self, forKey: .createdAt)
        )
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(projectID, forKey: .projectID)
        try c.encode(name, forKey: .name)
        try c.encode(parentAttemptID, forKey: .parentAttemptID)
        try c.encode(source, forKey: .source)
        try c.encode(premadeBucket, forKey: .premadeBucket)
        try c.encode(continuityScore, forKey: .continuityScore)
        try c.encode(clips, forKey: .clips)
        try c.encode(needsReview, forKey: .needsReview)
        try c.encode(createdAt, forKey: .createdAt)
    }
}

extension VoiceAnnotation: Codable {
    enum CodingKeys: String, CodingKey {
        case sourceID = "source_id"
        case timestampSec = "timestamp_sec"
        case text
        case resolvedClipID = "resolved_clip_id"
        case targetProjectID = "target_project_id"
        case targetTagID = "target_tag_id"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            sourceID: try c.decode(String.self, forKey: .sourceID),
            timestampSec: try c.decode(Double.self, forKey: .timestampSec),
            text: try c.decode(String.self, forKey: .text),
            resolvedClipID: try c.decodeIfPresent(String.self, forKey: .resolvedClipID),
            targetProjectID: try c.decodeIfPresent(String.self, forKey: .targetProjectID),
            targetTagID: try c.decodeIfPresent(String.self, forKey: .targetTagID)
        )
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(sourceID, forKey: .sourceID)
        try c.encode(timestampSec, forKey: .timestampSec)
        try c.encode(text, forKey: .text)
        try c.encode(resolvedClipID, forKey: .resolvedClipID)
        try c.encode(targetProjectID, forKey: .targetProjectID)
        try c.encode(targetTagID, forKey: .targetTagID)
    }
}
