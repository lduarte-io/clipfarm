import CFDomain
import Foundation
import GRDB

/// The ingest orchestrator — port of the reference `ingest.py` with the N3
/// native additions (D15 `.mkv` remux, D17 native probe fields, D18
/// settings-driven segmentation).
///
/// Shape: **pass 1** (async, zero writes) scans the folder, remuxes `.mkv`s,
/// loads sidecars, and probes — producing a plan; **pass 2** (sync,
/// `@MainActor`) applies every store write inside ONE undo group ("Ingest
/// Folder", N3 PROVISIONAL 4) so no `await` ever suspends between undo
/// registrations.
///
/// Re-ingest semantics (ported):
/// - source known, transcript was nil, sidecar now valid → upgrade + segment;
/// - source known, transcript present → skip;
/// - new source → add (+ segment when a valid sidecar exists);
/// - sidecar problems are non-fatal: rejection recorded, source still added
///   footage-only (re-running transcribe.py + re-ingest upgrades in place);
/// - `__` in the filename stem → hard reject with a sanitized-rename offer;
/// - a failed `.mkv` remux → hard reject (`remux-failed`, PROVISIONAL 5).

// MARK: - Result types

public struct IngestRejection: Equatable, Sendable {
    public enum Reason: String, Equatable, Sendable {
        case filenameContainsSeparator = "filename-contains-__"
        case schemaVersionMismatch = "schema-version-mismatch"
        case transcriptMalformed = "transcript-malformed"
        case transcriptUnreadable = "transcript-unreadable"
        /// Native addition (D15): the `.mkv` couldn't be remuxed — nothing
        /// playable exists to register.
        case remuxFailed = "remux-failed"
    }

    public var filename: String
    public var reason: Reason
    public var sanitizedRename: String?
    public var detail: String

    public init(filename: String, reason: Reason, sanitizedRename: String? = nil, detail: String = "") {
        self.filename = filename
        self.reason = reason
        self.sanitizedRename = sanitizedRename
        self.detail = detail
    }
}

public struct IngestResult: Equatable, Sendable {
    public var sourcesAdded: [String] = []
    public var sourcesSkipped: [String] = []
    public var sourcesUpdated: [String] = []
    public var rejected: [IngestRejection] = []
    public var warnings: [String] = []
    public var clipsDetected: Int = 0

    public init() {}
}

public enum IngestError: Error, Equatable {
    case notADirectory(String)
}

/// Probe seam: CFMedia's `MetadataProbe` adapter in the app; a stub in
/// tests. `nil` = probe failure — non-fatal, the source ingests with nil
/// fps/duration (the ported ffprobe-failure semantics).
public typealias SourceProbe = @Sendable (URL) async -> ProbedSourceInfo?
/// Remux seam: CFExport's `MKVRemuxer` in the app; a stub in tests.
/// Returns the playable sibling `.mp4` URL.
public typealias MKVRemux = @Sendable (URL) async throws -> URL

// MARK: - Orchestrator

extension LibraryStore {
    /// Acceptable video extensions (ported; lowercase compare).
    public static let videoExtensions: Set<String> = ["mov", "mp4", "m4v", "mkv"]

    @MainActor
    public func ingestFolder(
        at folderURL: URL,
        probe: SourceProbe,
        remux: MKVRemux
    ) async throws -> IngestResult {
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: folderURL.path, isDirectory: &isDirectory),
              isDirectory.boolValue
        else {
            throw IngestError.notADirectory(folderURL.path)
        }

        var result = IngestResult()
        let settings = try librarySettings()
        let stamp = iso8601(now())

        // Existing sources indexed by resolved path (O(1) re-ingest checks).
        var knownByPath: [String: (id: String, source: Source)] = [:]
        for record in try fetchAllSourceRecords() {
            knownByPath[Self.resolvedPath(record.source.path)] = (record.id, record.source)
        }

        // -------- Pass 1: async I/O, zero store writes --------
        enum PlannedAction {
            case add(source: Source, stem: String, transcript: WhisperTranscript?)
            case upgrade(sourceID: String, updated: Source, stem: String, transcript: WhisperTranscript)
        }
        var planned: [PlannedAction] = []
        var plannedPaths: Set<String> = []

        // .skipsHiddenFiles (cold-review finding 2): keeps AppleDouble
        // `._*.mov` litter out AND makes the remuxer's dot-prefixed-temp
        // contract true — a crash-orphaned `.stem.remux-*.mp4` must never
        // ingest as a garbage source.
        let files = (try FileManager.default.contentsOfDirectory(
            at: folderURL,
            includingPropertiesForKeys: [.isRegularFileKey],
            options: [.skipsHiddenFiles]
        ))
        .filter { url in
            (try? url.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true
                && Self.videoExtensions.contains(url.pathExtension.lowercased())
        }
        .sorted { $0.lastPathComponent < $1.lastPathComponent }

        for scannedURL in files {
            let scannedName = scannedURL.lastPathComponent
            let stem = scannedURL.deletingPathExtension().lastPathComponent

            guard ClipID.stemIsValid(stem) else {
                result.rejected.append(IngestRejection(
                    filename: scannedName,
                    reason: .filenameContainsSeparator,
                    sanitizedRename: "\(ClipID.sanitizedStem(stem)).\(scannedURL.pathExtension)",
                    detail: "Source filenames cannot contain '__' — it is the "
                        + "clip-ID separator. Rename the file and re-ingest."
                ))
                continue
            }

            // D15: Matroska is remuxed to a playable sibling .mp4 before
            // anything else looks at the file.
            var playableURL = scannedURL
            var originalPath: String?
            var adoptedPreexistingSibling = false
            if scannedURL.pathExtension.lowercased() == "mkv" {
                // Skip-if-exists means a same-stem sibling .mp4 is ADOPTED
                // as "an earlier remux" — if it's actually an unrelated
                // file, the adoption is wrong. Never silent (cold-review
                // finding 3): record the assumption in the result.
                adoptedPreexistingSibling = FileManager.default.fileExists(
                    atPath: scannedURL.deletingPathExtension()
                        .appendingPathExtension("mp4").path)
                do {
                    playableURL = try await remux(scannedURL)
                    originalPath = scannedURL.path
                } catch {
                    result.rejected.append(IngestRejection(
                        filename: scannedName,
                        reason: .remuxFailed,
                        detail: Self.remuxFailureDetail(error)
                    ))
                    continue
                }
            }

            let resolved = Self.resolvedPath(playableURL.path)
            let known = knownByPath[resolved]
            let alreadyPlanned = plannedPaths.contains(resolved)
            let playableName = playableURL.lastPathComponent

            // Warn once, when the adopted file first becomes a source — not
            // again on every idempotent re-ingest.
            if adoptedPreexistingSibling, known == nil, !alreadyPlanned {
                result.warnings.append(
                    "\(scannedName): adopted existing sibling \(playableName) "
                        + "as its remuxed copy (assumed to be an earlier remux — if it "
                        + "is an unrelated file, delete or rename it and re-ingest)")
            }

            // Sidecar pairing: `<stem>.whisper.json` next to the SCANNED
            // file (same stem serves an .mkv and its remuxed sibling).
            let sidecarURL = scannedURL.deletingLastPathComponent()
                .appendingPathComponent("\(stem).whisper.json")
            let sidecarExists = FileManager.default.fileExists(atPath: sidecarURL.path)

            var transcript: WhisperTranscript?
            if sidecarExists {
                switch Sidecar.load(at: sidecarURL) {
                case .ok(let loaded):
                    transcript = loaded
                case .rejected(let rejection):
                    result.rejected.append(rejection)
                    // Sidecar problems don't kill the source: register it
                    // footage-only (unless it already exists — untouched).
                    if known == nil, !alreadyPlanned {
                        let probed = await probe(playableURL)
                        planned.append(.add(
                            source: Self.makeSource(
                                playableURL: playableURL, resolvedPath: resolved,
                                originalPath: originalPath, probed: probed,
                                sidecarDuration: nil, transcriptPath: nil, addedAt: stamp
                            ),
                            stem: stem,
                            transcript: nil
                        ))
                        plannedPaths.insert(resolved)
                        result.sourcesAdded.append(scannedName)
                    }
                    continue
                }
            }

            if known == nil, !alreadyPlanned {
                let probed = await probe(playableURL)
                planned.append(.add(
                    source: Self.makeSource(
                        playableURL: playableURL, resolvedPath: resolved,
                        originalPath: originalPath, probed: probed,
                        sidecarDuration: transcript?.duration,
                        transcriptPath: sidecarExists ? Self.resolvedPath(sidecarURL.path) : nil,
                        addedAt: stamp
                    ),
                    stem: stem,
                    transcript: transcript
                ))
                plannedPaths.insert(resolved)
                result.sourcesAdded.append(scannedName)
                if transcript == nil {
                    result.warnings.append(
                        "\(playableName): no sidecar transcript — added as footage-only")
                }
                continue
            }

            if let known {
                if known.source.transcriptPath == nil, let transcript {
                    // Upgrade path: transcript newly available.
                    var updated = known.source
                    updated.transcriptPath = Self.resolvedPath(sidecarURL.path)
                    if updated.durationSec == nil { updated.durationSec = transcript.duration }
                    planned.append(.upgrade(
                        sourceID: known.id, updated: updated, stem: stem, transcript: transcript))
                    result.sourcesUpdated.append(scannedName)
                } else {
                    result.sourcesSkipped.append(scannedName)
                }
            } else {
                // Planned earlier in this same run — the .mkv + sibling
                // .mp4 pair resolving to one playable file.
                result.sourcesSkipped.append(scannedName)
            }
        }

        // -------- Pass 2: synchronous store writes, one undo group --------
        guard !planned.isEmpty else { return result }

        var existingClipIDs = try fetchAllClipIDs()

        undoManager?.beginUndoGrouping()
        defer {
            undoManager?.setActionName("Ingest Folder")
            undoManager?.endUndoGrouping()
        }

        for action in planned {
            switch action {
            case .add(let source, let stem, let transcript):
                let sourceID = try addSource(source)
                if let transcript {
                    let clips = try Self.plannedClips(
                        for: transcript, stem: stem, sourceID: sourceID,
                        durationSec: source.durationSec, settings: settings,
                        createdAt: stamp, existingIDs: &existingClipIDs
                    )
                    try addClips(clips)
                    result.clipsDetected += clips.count
                }
            case .upgrade(let sourceID, let updated, let stem, let transcript):
                try updateSource(id: sourceID, updated)
                let clips = try Self.plannedClips(
                    for: transcript, stem: stem, sourceID: sourceID,
                    durationSec: updated.durationSec, settings: settings,
                    createdAt: stamp, existingIDs: &existingClipIDs
                )
                try addClips(clips)
                result.clipsDetected += clips.count
            }
        }
        return result
    }

    // MARK: - Helpers

    /// Synchronous wrappers so the async orchestrator binds GRDB's sync
    /// `read` overload (the async one would suspend mid-plan for no reason).
    private func fetchAllSourceRecords() throws -> [SourceRecord] {
        try dbPool.read { try SourceRecord.fetchAll($0) }
    }

    private func fetchAllClipIDs() throws -> Set<String> {
        Set(try dbPool.read { try String.fetchAll($0, sql: "SELECT id FROM clips") })
    }

    static func resolvedPath(_ path: String) -> String {
        URL(fileURLWithPath: path).resolvingSymlinksInPath().standardizedFileURL.path
    }

    private static func makeSource(
        playableURL: URL,
        resolvedPath: String,
        originalPath: String?,
        probed: ProbedSourceInfo?,
        sidecarDuration: Double?,
        transcriptPath: String?,
        addedAt: String
    ) -> Source {
        Source(
            filename: playableURL.lastPathComponent,
            path: resolvedPath,
            originalPath: originalPath,
            // Duration policy (locked): sidecar wins → probe → nil.
            durationSec: sidecarDuration ?? probed?.durationSec,
            fps: probed?.fps,
            transcriptPath: transcriptPath,
            addedAt: addedAt,
            unavailable: false,
            isHDR: probed?.isHDR,
            naturalWidth: probed?.naturalWidth,
            naturalHeight: probed?.naturalHeight
        )
    }

    /// Segments a transcript into insertable clips, skipping IDs that
    /// already exist (idempotency — ported).
    static func plannedClips(
        for transcript: WhisperTranscript,
        stem: String,
        sourceID: String,
        durationSec: Double?,
        settings: LibrarySettings,
        createdAt: String,
        existingIDs: inout Set<String>
    ) throws -> [(id: String, clip: Clip)] {
        let words = transcript.allWords
        guard !words.isEmpty else { return [] }
        let ranges = try Segmentation.segment(
            words: words,
            gapThresholdSec: settings.silenceThresholdSec,
            tailPolicy: settings.tailPolicy,
            tailPaddingSec: settings.tailPaddingSec,
            sourceDurationSec: durationSec
        )
        var out: [(id: String, clip: Clip)] = []
        for range in ranges {
            let id = ClipID.make(sourceStem: stem, start: range.startSec, end: range.endSec)
            guard !existingIDs.contains(id) else { continue }
            existingIDs.insert(id)
            out.append((
                id,
                Clip(
                    sourceID: sourceID,
                    startSec: range.startSec,
                    endSec: range.endSec,
                    transcriptText: transcript.transcriptText(from: range.startSec, to: range.endSec),
                    createdAt: createdAt
                )
            ))
        }
        return out
    }

    private static func remuxFailureDetail(_ error: any Error) -> String {
        // CFStore can't see CFExport's error type (no dependency edge) —
        // String(describing:) carries the locator/stderr detail through.
        "Could not remux .mkv to .mp4: \(String(describing: error)). "
            + "Install ffmpeg (brew install ffmpeg) or set its path in Settings, then re-ingest."
    }
}

// MARK: - Sidecar loading (shared with the re-apply action)

enum Sidecar {
    enum LoadOutcome {
        case ok(WhisperTranscript)
        case rejected(IngestRejection)
    }

    static let supportedSchemaVersion = 1

    /// Port of `_load_sidecar`: failure modes split so callers report them
    /// individually; the schema version is checked before full validation
    /// so a version mismatch isn't misreported as malformed JSON.
    static func load(at url: URL) -> LoadOutcome {
        let data: Data
        do {
            data = try Data(contentsOf: url)
        } catch {
            return .rejected(IngestRejection(
                filename: "\(url.deletingLastPathComponent().lastPathComponent)/\(url.lastPathComponent)",
                reason: .transcriptUnreadable,
                detail: String(describing: error)
            ))
        }

        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return .rejected(IngestRejection(
                filename: url.lastPathComponent,
                reason: .transcriptMalformed,
                detail: "invalid JSON"
            ))
        }

        let schemaVersion = object["schema_version"] as? Int
        guard schemaVersion == supportedSchemaVersion else {
            return .rejected(IngestRejection(
                filename: url.lastPathComponent,
                reason: .schemaVersionMismatch,
                detail: "sidecar reports schema_version=\(schemaVersion.map(String.init) ?? "nil"); "
                    + "ClipFarm supports \(supportedSchemaVersion). Re-run transcribe.py or "
                    + "add an adapter migration."
            ))
        }

        do {
            return .ok(try JSONDecoder().decode(WhisperTranscript.self, from: data))
        } catch {
            return .rejected(IngestRejection(
                filename: url.lastPathComponent,
                reason: .transcriptMalformed,
                detail: String(describing: error)
            ))
        }
    }
}
