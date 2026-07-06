import CFDomain
import CFTestSupport
import Foundation
import GRDB
import Testing
@testable import CFStore

/// Port of `tests/test_uniqueness_validator.py` (7 tests) against the
/// store: domain validation is the enforcer (import + mutation paths); the
/// NULL-proof index is the backstop (covered in StoreOpenTests).
///
/// Fixture adaptation (recorded in the phase entry): `clip_project_tags
/// .clip_id` IS an FK in the native schema (plan §2.3), so referenced clips
/// exist here, unlike the reference fixtures.

private func stateWithTags(_ tags: [ClipProjectTag]) -> ClipFarmState {
    var state = Fixtures.stateWithClips([
        ("c1", "s1", 0.0, 1.0),
        ("c2", "s1", 1.0, 2.0),
    ])
    state.clipProjectTags = tags
    return state
}

private func tag(
    clipID: String = "c1",
    projectID: String = "p1",
    projectTagID: String? = "t1",
    category: ClipCategory = .onScript
) -> ClipProjectTag {
    ClipProjectTag(clipID: clipID, projectID: projectID, projectTagID: projectTagID, category: category)
}

@MainActor @Test func duplicateFullKeyIsRejectedOnImport() throws {
    // Explicit `throws`: the closure's only visible `try`s sit inside
    // #expect macros, which throws-inference can't see.
    try withScratchStore { (store) throws in
        #expect(throws: ClipProjectTagUniquenessError.self) {
            try store.importState(stateWithTags([tag(), tag()]))
        }
        // The failed import must not have landed partial rows.
        #expect(try store.fetchState().clipProjectTags.isEmpty)
    }
}

@MainActor @Test func duplicateWithNilProjectTagIDIsRejected() throws {
    // nil is a value, not a uniqueness bypass.
    try withScratchStore { store in
        #expect(throws: ClipProjectTagUniquenessError.self) {
            try store.importState(stateWithTags([
                tag(projectTagID: nil),
                tag(projectTagID: nil),
            ]))
        }
    }
}

@MainActor @Test func differentCategorySameTagIsNotADuplicate() throws {
    // A clip can be on-script AND standalone-idea for the same line tag.
    try withScratchStore { store in
        try store.importState(stateWithTags([
            tag(category: .onScript),
            tag(category: .standaloneIdea),
        ]))
        #expect(try store.fetchState().clipProjectTags.count == 2)
    }
}

@MainActor @Test func differentClipSameTagIsNotADuplicate() throws {
    try withScratchStore { store in
        try store.importState(stateWithTags([
            tag(clipID: "c1"),
            tag(clipID: "c2"),
        ]))
        #expect(try store.fetchState().clipProjectTags.count == 2)
    }
}

@MainActor @Test func differentProjectSameTagIsNotADuplicate() throws {
    // Multi-project tagging: the same clip in two projects with the same
    // triple is fine — project_id differs.
    try withScratchStore { store in
        try store.importState(stateWithTags([
            tag(projectID: "p1"),
            tag(projectID: "p2"),
        ]))
        #expect(try store.fetchState().clipProjectTags.count == 2)
    }
}

@MainActor @Test func mutationPathRejectsDuplicateAgainstExistingRows() throws {
    // The addClipProjectTag path validates against what's already stored.
    try withScratchStore { store in
        try store.importState(stateWithTags([tag()]))
        #expect(throws: ClipProjectTagUniquenessError.self) {
            try store.addClipProjectTag(tag())
        }
        // Different category on the same tag id passes.
        try store.addClipProjectTag(tag(category: .standaloneIdea))
        #expect(try store.fetchState().clipProjectTags.count == 2)
    }
}

@MainActor @Test func cleanStateRoundTripsThroughTheValidator() throws {
    try withScratchStore { store in
        try store.importState(stateWithTags([
            tag(clipID: "c1"),
            tag(clipID: "c2"),
            tag(clipID: "c1", category: .standaloneIdea),
        ]))
        #expect(try store.fetchState().clipProjectTags.count == 3)
    }
}
