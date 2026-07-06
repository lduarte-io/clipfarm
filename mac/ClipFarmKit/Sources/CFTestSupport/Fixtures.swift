import CFDomain

/// Fixture builders shared by the ClipFarmKit test targets ("fixture
/// builders for everything downstream" — plan N1). Mirrors the reference
/// suite's `_state()` helpers so ported tests read like their originals.
///
/// Never ships in the app: nothing in the library product depends on this
/// target; only test targets do.
public enum Fixtures {
    /// Deterministic timestamp — domain logic never branches on it.
    public static let timestamp = "2026-07-06T00:00:00+00:00"

    public static func source(
        filename: String = "src.mov",
        path: String = "/nonexistent/src.mov",
        durationSec: Double? = nil,
        fps: Double? = nil,
        transcriptPath: String? = nil,
        unavailable: Bool = true
    ) -> Source {
        Source(
            filename: filename,
            path: path,
            durationSec: durationSec,
            fps: fps,
            transcriptPath: transcriptPath,
            addedAt: timestamp,
            unavailable: unavailable
        )
    }

    public static func clip(
        sourceID: String,
        startSec: Double,
        endSec: Double,
        transcriptText: String = ""
    ) -> Clip {
        Clip(
            sourceID: sourceID,
            startSec: startSec,
            endSec: endSec,
            transcriptText: transcriptText,
            createdAt: timestamp
        )
    }

    public static func attempt(
        projectID: String = "p1",
        name: String = "test",
        source: AttemptSource = .handBuilt,
        continuityScore: Double? = nil,
        clips: [AttemptClip] = []
    ) -> Attempt {
        Attempt(
            projectID: projectID,
            name: name,
            source: source,
            continuityScore: continuityScore,
            clips: clips,
            createdAt: timestamp
        )
    }

    /// Port of the reference tests' `_state()` shape:
    /// `sources` = (id, durationSec); `clips` = (id, sourceID, start, end);
    /// one attempt `attemptID` with `attemptClips`.
    public static func state(
        sources: [(id: String, durationSec: Double?)] = [(id: "s1", durationSec: 100.0)],
        clips: [(id: String, sourceID: String, start: Double, end: Double)] = [],
        attemptID: String = "a1",
        attemptClips: [AttemptClip]? = nil
    ) -> ClipFarmState {
        var state = ClipFarmState()
        for spec in sources {
            state.sources[spec.id] = source(
                filename: "src\(spec.id).mov",
                path: "/src\(spec.id).mov",
                durationSec: spec.durationSec
            )
        }
        for spec in clips {
            state.clips[spec.id] = clip(
                sourceID: spec.sourceID, startSec: spec.start, endSec: spec.end
            )
        }
        if let attemptClips {
            state.attempts[attemptID] = attempt(clips: attemptClips)
        }
        return state
    }

    /// Port of `_state_with_clips` from the continuity tests: sources are
    /// derived from the clip specs.
    public static func stateWithClips(
        _ specs: [(id: String, sourceID: String, start: Double, end: Double)]
    ) -> ClipFarmState {
        var state = ClipFarmState()
        for spec in specs where state.sources[spec.sourceID] == nil {
            state.sources[spec.sourceID] = source(
                filename: "src\(spec.sourceID).mov",
                path: "/src\(spec.sourceID).mov"
            )
        }
        for spec in specs {
            state.clips[spec.id] = clip(
                sourceID: spec.sourceID, startSec: spec.start, endSec: spec.end
            )
        }
        return state
    }

    /// A transcript with one segment holding `words` = (start, end, text) —
    /// port of `_state_with_transcript`'s sidecar shape.
    public static func transcript(
        words: [(start: Double, end: Double, word: String)]
    ) -> WhisperTranscript {
        WhisperTranscript(
            schemaVersion: 1,
            duration: (words.last?.end).map { $0 + 1.0 } ?? 1.0,
            segments: [
                WhisperSegment(
                    id: 0,
                    start: words.first?.start ?? 0,
                    end: words.last?.end ?? 0,
                    words: words.map { WhisperWord(start: $0.start, end: $0.end, word: $0.word) }
                )
            ]
        )
    }

    /// One of everything with every optional populated — the round-trip
    /// worst case. `tracks` stays nil (writer invariant: NULL until N18).
    public static func fullState() -> ClipFarmState {
        var state = ClipFarmState()
        state.sources["1"] = Source(
            filename: "btc.0.4.mov",
            path: "/footage/btc.0.4.mov",
            durationSec: 1812.34,
            fps: 60.0,
            transcriptPath: "/footage/btc.0.4.whisper.json",
            addedAt: timestamp,
            unavailable: true,
            isHDR: true,
            naturalWidth: 3840,
            naturalHeight: 2160
        )
        let clipID = "btc.0.4__00-01-12.345__00-01-18.220"
        state.clips[clipID] = Clip(
            sourceID: "1",
            startSec: 72.345,
            endSec: 78.220,
            transcriptText: " the hook line",
            derivedFromClipID: "btc.0.4__00-01-00.000__00-01-30.000",
            boundaryEdited: true,
            tracks: nil,
            createdAt: timestamp
        )
        state.projects["1"] = Project(
            name: "btc explainer v0.4",
            briefMD: "# brief\nscript:\n- the hook",
            script: Script(lines: ["the hook", "the setup"]),
            tags: [
                "1": ProjectTag(kind: .section, name: "intro", parentID: nil, orderIdx: 0),
                "2": ProjectTag(kind: .line, name: "the hook", parentID: "1", orderIdx: 0),
                "3": ProjectTag(kind: .tag, name: "self-custody", parentID: nil, orderIdx: 0),
            ],
            createdAt: timestamp
        )
        state.clipProjectTags = [
            ClipProjectTag(
                clipID: clipID,
                projectID: "1",
                projectTagID: "2",
                category: .onScript,
                confidence: 0.92,
                source: .ai,
                stale: true,
                notes: "solid take"
            ),
            ClipProjectTag(
                clipID: clipID,
                projectID: "1",
                projectTagID: nil,
                category: .standaloneIdea,
                confidence: 0.5,
                source: .user
            ),
        ]
        state.attempts["1"] = Attempt(
            projectID: "1",
            name: "the 3 times you said it in almost one take",
            parentAttemptID: "0",
            source: .aiPremade,
            premadeBucket: .best,
            continuityScore: 0.92,
            clips: [
                AttemptClip(
                    clipID: clipID,
                    trimStartOffset: 0.25,
                    trimEndOffset: -0.5,
                    internalPauseMaxSec: 0.5,
                    notes: "tightened"
                ),
                AttemptClip(clipID: "deleted__00-00-00.000__00-00-01.000"),
            ],
            needsReview: true,
            createdAt: timestamp
        )
        state.voiceAnnotations = [
            VoiceAnnotation(
                sourceID: "1",
                timestampSec: 345.67,
                text: "good line, save for section C",
                resolvedClipID: clipID,
                targetProjectID: "1",
                targetTagID: "1"
            )
        ]
        return state
    }
}
