/// The `.whisper.json` sidecar shape ClipFarm consumes (never produces in
/// Track 1). Pinned to `schema_version == 1`; ingest (N3) refuses any other
/// version with a clear error pointing at `transcribe.py`.
///
/// Word strings carry a leading space when present (faster_whisper
/// convention) — concatenate raw, never add separators.

public struct WhisperWord: Equatable, Sendable {
    public var start: Double
    public var end: Double
    public var word: String
    public var probability: Double?

    public init(start: Double, end: Double, word: String, probability: Double? = nil) {
        self.start = start
        self.end = end
        self.word = word
        self.probability = probability
    }
}

public struct WhisperSegment: Equatable, Sendable {
    public var id: Int?
    public var start: Double
    public var end: Double
    public var text: String?
    public var words: [WhisperWord]

    public init(id: Int? = nil, start: Double, end: Double, text: String? = nil, words: [WhisperWord] = []) {
        self.id = id
        self.start = start
        self.end = end
        self.text = text
        self.words = words
    }
}

public struct WhisperTranscript: Equatable, Sendable {
    public var schemaVersion: Int
    public var sourceFilename: String?
    public var language: String?
    public var languageProbability: Double?
    public var duration: Double?
    public var model: String?
    public var transcribedAt: String?
    public var segments: [WhisperSegment]

    public init(
        schemaVersion: Int,
        sourceFilename: String? = nil,
        language: String? = nil,
        languageProbability: Double? = nil,
        duration: Double? = nil,
        model: String? = nil,
        transcribedAt: String? = nil,
        segments: [WhisperSegment] = []
    ) {
        self.schemaVersion = schemaVersion
        self.sourceFilename = sourceFilename
        self.language = language
        self.languageProbability = languageProbability
        self.duration = duration
        self.model = model
        self.transcribedAt = transcribedAt
        self.segments = segments
    }

    /// All words across all segments, in sidecar order (chronological by
    /// Whisper's construction). The resolver walks this flattened view.
    public var allWords: [WhisperWord] {
        segments.flatMap(\.words)
    }
}

// MARK: - Codable (sidecar keys are already snake_case)

extension WhisperWord: Codable {
    enum CodingKeys: String, CodingKey {
        case start, end, word, probability
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            start: try c.decode(Double.self, forKey: .start),
            end: try c.decode(Double.self, forKey: .end),
            word: try c.decode(String.self, forKey: .word),
            probability: try c.decodeIfPresent(Double.self, forKey: .probability)
        )
    }
}

extension WhisperSegment: Codable {
    enum CodingKeys: String, CodingKey {
        case id, start, end, text, words
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            id: try c.decodeIfPresent(Int.self, forKey: .id),
            start: try c.decode(Double.self, forKey: .start),
            end: try c.decode(Double.self, forKey: .end),
            text: try c.decodeIfPresent(String.self, forKey: .text),
            words: try c.decodeIfPresent([WhisperWord].self, forKey: .words) ?? []
        )
    }
}

extension WhisperTranscript: Codable {
    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case sourceFilename = "source_filename"
        case language
        case languageProbability = "language_probability"
        case duration
        case model
        case transcribedAt = "transcribed_at"
        case segments
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            schemaVersion: try c.decode(Int.self, forKey: .schemaVersion),
            sourceFilename: try c.decodeIfPresent(String.self, forKey: .sourceFilename),
            language: try c.decodeIfPresent(String.self, forKey: .language),
            languageProbability: try c.decodeIfPresent(Double.self, forKey: .languageProbability),
            duration: try c.decodeIfPresent(Double.self, forKey: .duration),
            model: try c.decodeIfPresent(String.self, forKey: .model),
            transcribedAt: try c.decodeIfPresent(String.self, forKey: .transcribedAt),
            segments: try c.decodeIfPresent([WhisperSegment].self, forKey: .segments) ?? []
        )
    }
}
