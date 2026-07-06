import CFDomain
import CFMediaTestSupport
import Foundation

/// Tiny synthetic movies for the integration tests — rendered once per test
/// run into a temp directory (self-contained: no ffmpeg, no network, no
/// footage-folder dependency; `swift test` runs anywhere).
actor TestFixtures {
    static let shared = TestFixtures()

    private let directory = FileManager.default.temporaryDirectory
        .appendingPathComponent("cfmedia-test-fixtures-\(ProcessInfo.processInfo.processIdentifier)")

    func url(for spec: MediaFixtureSpec) async throws -> URL {
        try await MediaFixtureRenderer.render(spec, in: directory)
    }
}

enum TinySpec {
    static let h264 = MediaFixtureSpec(
        name: "tiny-h264", codec: .h264, width: 160, height: 96,
        fps: 30, durationSec: 2.0, keyframeIntervalFrames: 30, grayLevel: 118)
    static let h264B = MediaFixtureSpec(
        name: "tiny-h264-b", codec: .h264, width: 160, height: 96,
        fps: 30, durationSec: 2.0, keyframeIntervalFrames: 30, grayLevel: 140)
    static let portrait = MediaFixtureSpec(
        name: "tiny-portrait", codec: .h264, width: 160, height: 96,
        fps: 30, durationSec: 2.0, rotated90: true, grayLevel: 118)
    static let hlg = MediaFixtureSpec(
        name: "tiny-hlg", codec: .hevc10HLG, width: 160, height: 96,
        fps: 30, durationSec: 2.0, grayLevel: 118)
    static let videoOnly = MediaFixtureSpec(
        name: "tiny-video-only", codec: .h264, width: 160, height: 96,
        fps: 30, durationSec: 2.0, grayLevel: 118, audio: FixtureAudio.none)
}

/// The resolver-range shape the engine consumes (N1 delta #1) — built here
/// so PlannerTests stays free of CFDomain fixture noise.
func CFDomainResolvedRangeFixture() -> ResolvedRange {
    ResolvedRange(
        clipID: "src__00-00-03.250__00-00-07.500",
        sourceID: "1",
        effectiveStartSec: 3.25,
        effectiveEndSec: 7.5
    )
}
