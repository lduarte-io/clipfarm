import CFTestSupport
import Foundation
import Testing
@testable import CFDomain

/// Construction-default half of the ported `test_models_round_trip.py`
/// (the disk half lives in CFStoreTests), plus the Codable
/// decode-with-defaults contract that backs the fixture loader and the
/// N13 tolerant restore.

// MARK: - Construction defaults

@Test func clipTracksDefaultsToNil() {
    let clip = Fixtures.clip(sourceID: "1", startSec: 0.0, endSec: 1.0)
    #expect(clip.tracks == nil)
    #expect(clip.boundaryEdited == false)
}

@Test func attemptOptionalFieldsDefaultToNil() {
    let attempt = Fixtures.attempt()
    #expect(attempt.continuityScore == nil)
    #expect(attempt.premadeBucket == nil)
    #expect(attempt.needsReview == false)
    #expect(attempt.parentAttemptID == nil)
}

@Test func attemptClipInternalPauseDefaultsToNil() {
    let ac = AttemptClip(clipID: "cid")
    #expect(ac.internalPauseMaxSec == nil)
    #expect(ac.trimStartOffset == 0.0)
    #expect(ac.trimEndOffset == 0.0)
}

@Test func clipProjectTagDefaults() {
    let tag = ClipProjectTag(clipID: "c", projectID: "p", category: .onScript)
    #expect(tag.projectTagID == nil)
    #expect(tag.confidence == 1.0)
    #expect(tag.source == .user)
    #expect(tag.stale == false)
    #expect(tag.notes.isEmpty)
}

// MARK: - Codable (documented snake_case JSON shape)

@Test func fullStateRoundTripsThroughJSON() throws {
    let state = Fixtures.fullState()
    let data = try JSONEncoder().encode(state)
    let decoded = try JSONDecoder().decode(ClipFarmState.self, from: data)
    #expect(decoded == state)
}

@Test func clipDecodesLegacyJSONWithMissingOptionalKeys() throws {
    // Legacy clipfarm.json predates `boundary_edited`; defaults apply on
    // decode (the tolerant direction the fixture loader depends on).
    let json = """
        {"source_id": "1", "start_sec": 72.345, "end_sec": 78.22, "created_at": "t"}
        """
    let clip = try JSONDecoder().decode(Clip.self, from: Data(json.utf8))
    #expect(clip.transcriptText.isEmpty)
    #expect(clip.boundaryEdited == false)
    #expect(clip.tracks == nil)
    #expect(clip.derivedFromClipID == nil)
}

@Test func encodedClipWritesSnakeCaseKeysAndExplicitNulls() throws {
    let clip = Fixtures.clip(sourceID: "1", startSec: 0.0, endSec: 1.0)
    let data = try JSONEncoder().encode(clip)
    let object = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
    #expect(object["source_id"] as? String == "1")
    #expect(object.keys.contains("tracks"))
    #expect(object["tracks"] is NSNull)  // null, not missing (backup contract)
    #expect(object.keys.contains("derived_from_clip_id"))
}

@Test func stateDecodesFromDocumentedSpecShape() throws {
    // The spec's own data-model example shape (abbreviated).
    let json = """
        {
          "version": 1,
          "sources": {
            "1": {"filename": "btc.0.4.mov", "path": "/x/btc.0.4.mov",
                  "duration_sec": 1812.34, "fps": 60.0,
                  "transcript_path": "/x/btc.0.4.whisper.json", "added_at": "t"}
          },
          "clips": {
            "btc.0.4__00-01-12.345__00-01-18.220": {
              "source_id": "1", "start_sec": 72.345, "end_sec": 78.220,
              "transcript_text": "...", "derived_from_clip_id": null,
              "tracks": null, "created_at": "t"
            }
          },
          "projects": {
            "1": {"name": "btc explainer v0.4", "brief_md": "...",
                  "script": {"lines": ["..."]},
                  "tags": {"1": {"kind": "section", "name": "intro",
                                 "parent_id": null, "order_idx": 0}},
                  "created_at": "t"}
          },
          "clip_project_tags": [
            {"clip_id": "btc.0.4__00-01-12.345__00-01-18.220", "project_id": "1",
             "project_tag_id": "2", "category": "on-script", "confidence": 0.92,
             "source": "ai", "stale": false, "notes": ""}
          ],
          "attempts": {
            "1": {"project_id": "1", "name": "n", "parent_attempt_id": null,
                  "source": "ai-premade", "premade_bucket": "best",
                  "continuity_score": 0.92,
                  "clips": [{"clip_id": "btc.0.4__00-01-12.345__00-01-18.220",
                             "trim_start_offset": 0.0, "trim_end_offset": 0.0,
                             "internal_pause_max_sec": null, "notes": ""}],
                  "created_at": "t"}
          },
          "voice_annotations": []
        }
        """
    let state = try JSONDecoder().decode(ClipFarmState.self, from: Data(json.utf8))
    #expect(state.sources["1"]?.fps == 60.0)
    #expect(state.clips.count == 1)
    #expect(state.projects["1"]?.tags["1"]?.kind == .section)
    #expect(state.clipProjectTags.first?.category == .onScript)
    #expect(state.attempts["1"]?.premadeBucket == .best)
    #expect(state.attempts["1"]?.source == .aiPremade)
    // needs_review is a native addition, absent from legacy JSON → default.
    #expect(state.attempts["1"]?.needsReview == false)
}

@Test func whisperTranscriptDecodesSidecarShape() throws {
    let json = """
        {
          "schema_version": 1,
          "source_filename": "btc.0.4.mov",
          "duration": 2059.84,
          "segments": [
            {"id": 1, "start": 4.37, "end": 27.39, "text": " She makes",
             "words": [
               {"start": 4.37, "end": 4.69, "word": " She", "probability": 0.4681},
               {"start": 4.69, "end": 4.93, "word": " makes", "probability": 0.9718}
             ]}
          ]
        }
        """
    let transcript = try JSONDecoder().decode(WhisperTranscript.self, from: Data(json.utf8))
    #expect(transcript.schemaVersion == 1)
    #expect(transcript.allWords.count == 2)
    // Leading-space word convention survives (concatenate raw).
    #expect(transcript.allWords.first?.word == " She")
}

// MARK: - Uniqueness (domain rule)

@Test func duplicateClipProjectTagKeyThrows() {
    let tag = ClipProjectTag(clipID: "c1", projectID: "p1", projectTagID: "t1", category: .onScript)
    #expect(throws: ClipProjectTagUniquenessError.self) {
        try validateClipProjectTagUniqueness([tag, tag])
    }
}

@Test func nilProjectTagIDIsAValueNotABypass() {
    let tag = ClipProjectTag(clipID: "c1", projectID: "p1", projectTagID: nil, category: .onScript)
    #expect(throws: ClipProjectTagUniquenessError.self) {
        try validateClipProjectTagUniqueness([tag, tag])
    }
}

@Test func differentCategorySameTagIsNotDuplicate() throws {
    // A clip can be on-script AND standalone-idea for the same line tag.
    try validateClipProjectTagUniqueness([
        ClipProjectTag(clipID: "c1", projectID: "p1", projectTagID: "t1", category: .onScript),
        ClipProjectTag(clipID: "c1", projectID: "p1", projectTagID: "t1", category: .standaloneIdea),
    ])
}
