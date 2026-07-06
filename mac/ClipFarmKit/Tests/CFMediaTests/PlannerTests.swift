import CoreMedia
import Foundation
import Testing
@testable import CFMedia

/// Pure composition-planning rules — the logic testable without hardware
/// timing (gate measurements are the N2 harness's job, not unit tests).

// MARK: - MediaTime (D12)

@Test func mediaTimeConvertsAtTimescale600() {
    let t = MediaTime.time(1.5)
    #expect(t.value == 900)
    #expect(t.timescale == 600)
    #expect(MediaTime.seconds(t) == 1.5)
}

// MARK: - Rule 2: clamped insert range

private func range(_ start: Double, _ end: Double) -> CMTimeRange {
    CMTimeRange(start: MediaTime.time(start), end: MediaTime.time(end))
}

@Test func clampPullsRequestedEndToShortestTrack() {
    let clamped = CompositionPlanner.clampedInsertRange(
        requested: range(8, 12), videoRange: range(0, 10), audioRange: range(0, 9.5))
    #expect(clamped == range(8, 9.5))
}

@Test func clampWithoutAudioTrackUsesVideoOnly() {
    let clamped = CompositionPlanner.clampedInsertRange(
        requested: range(8, 12), videoRange: range(0, 10), audioRange: nil)
    #expect(clamped == range(8, 10))
}

@Test func clampToNothingReturnsNil() {
    let clamped = CompositionPlanner.clampedInsertRange(
        requested: range(9.6, 12), videoRange: range(0, 10), audioRange: range(0, 9.5))
    #expect(clamped == nil)
}

@Test func fullyInsideRequestIsUntouched() {
    let clamped = CompositionPlanner.clampedInsertRange(
        requested: range(1, 2), videoRange: range(0, 10), audioRange: range(0, 10))
    #expect(clamped == range(1, 2))
}

// MARK: - D32 geometry uniformity

private func info(
    size: CGSize, transform: CGAffineTransform = .identity, isHDR: Bool = false
) -> VideoTrackInfo {
    VideoTrackInfo(
        naturalSize: size, preferredTransform: transform,
        minFrameDuration: CMTime(value: 1, timescale: 30), nominalFrameRate: 30,
        timeRange: range(0, 10), codec: "avc1",
        colorPrimaries: nil, transferFunction: nil, isHDR: isHDR
    )
}

@Test func uniformGeometryDetected() {
    let a = info(size: CGSize(width: 1920, height: 1080))
    let b = info(size: CGSize(width: 1920, height: 1080))
    #expect(CompositionPlanner.isGeometryUniform([a, b]))
}

@Test func mixedRotationIsNotUniform() {
    let landscape = info(size: CGSize(width: 1920, height: 1080))
    let portrait = info(
        size: CGSize(width: 1920, height: 1080),
        transform: CGAffineTransform(rotationAngle: .pi / 2))
    #expect(!CompositionPlanner.isGeometryUniform([landscape, portrait]))
}

@Test func mixedSizesAreNotUniform() {
    let hd = info(size: CGSize(width: 1920, height: 1080))
    let uhd = info(size: CGSize(width: 3840, height: 2160))
    #expect(!CompositionPlanner.isGeometryUniform([hd, uhd]))
}

@Test func emptyAndSingleSegmentAreUniform() {
    #expect(CompositionPlanner.isGeometryUniform([]))
    #expect(CompositionPlanner.isGeometryUniform([info(size: CGSize(width: 1, height: 1))]))
}

// MARK: - D29 dynamic-range mix

@Test func dynamicRangeMixDetection() {
    let sdr = info(size: CGSize(width: 1920, height: 1080))
    let hdr = info(size: CGSize(width: 1920, height: 1080), isHDR: true)
    #expect(CompositionPlanner.dynamicRangesMix([sdr, hdr]))
    #expect(!CompositionPlanner.dynamicRangesMix([sdr, sdr]))
    #expect(!CompositionPlanner.dynamicRangesMix([hdr, hdr]))
}

// MARK: - D32 fit transform (pillarbox default)

@Test func portraitClipPillarboxesIntoLandscapeCanvas() {
    // 1920×1080 encoded, rotated 90° → oriented 1080×1920; into a
    // 1920×1080 canvas → scale 0.5625, centered: x ∈ [656.25, 1263.75].
    let transform = CompositionPlanner.fitTransform(
        naturalSize: CGSize(width: 1920, height: 1080),
        preferredTransform: CGAffineTransform(rotationAngle: .pi / 2),
        canvas: CGSize(width: 1920, height: 1080)
    )
    let mapped = CGRect(x: 0, y: 0, width: 1920, height: 1080).applying(transform)
    #expect(abs(mapped.minX - 656.25) < 0.001)
    #expect(abs(mapped.maxX - 1263.75) < 0.001)
    #expect(abs(mapped.minY - 0) < 0.001)
    #expect(abs(mapped.maxY - 1080) < 0.001)
}

@Test func matchingGeometryIsIdentityFit() {
    let transform = CompositionPlanner.fitTransform(
        naturalSize: CGSize(width: 1920, height: 1080),
        preferredTransform: .identity,
        canvas: CGSize(width: 1920, height: 1080)
    )
    #expect(transform == .identity)
}

@Test func smallerClipScalesUpToCanvas() {
    let transform = CompositionPlanner.fitTransform(
        naturalSize: CGSize(width: 1280, height: 720),
        preferredTransform: .identity,
        canvas: CGSize(width: 1920, height: 1080)
    )
    let mapped = CGRect(x: 0, y: 0, width: 1280, height: 720).applying(transform)
    #expect(abs(mapped.width - 1920) < 0.001)
    #expect(abs(mapped.height - 1080) < 0.001)
    #expect(abs(mapped.minX) < 0.001)
}

// MARK: - D31 fade ramps

private func segment(_ start: Double, _ end: Double) -> BuiltSegment {
    BuiltSegment(
        range: PlayableRange(
            url: URL(fileURLWithPath: "/dev/null"), startSec: 0, endSec: end - start),
        compositionStart: MediaTime.time(start),
        duration: MediaTime.time(end - start),
        sourceStart: .zero
    )
}

@Test func singleSegmentHasNoRamps() {
    #expect(CompositionPlanner.fadeRamps(segments: [segment(0, 2)]).isEmpty)
}

@Test func internalBoundaryGetsDownThenUpRamp() {
    let ramps = CompositionPlanner.fadeRamps(segments: [segment(0, 1), segment(1, 2)])
    #expect(ramps.count == 2)
    let tenMS = CMTime(value: 6, timescale: 600)
    #expect(ramps[0].fromVolume == 1 && ramps[0].toVolume == 0)
    #expect(ramps[0].timeRange == CMTimeRange(start: MediaTime.time(1) - tenMS, end: MediaTime.time(1)))
    #expect(ramps[1].fromVolume == 0 && ramps[1].toVolume == 1)
    #expect(ramps[1].timeRange == CMTimeRange(start: MediaTime.time(1), duration: tenMS))
}

@Test func threeSegmentsGetFourRamps() {
    let ramps = CompositionPlanner.fadeRamps(
        segments: [segment(0, 1), segment(1, 2), segment(2, 3)])
    #expect(ramps.count == 4)
}

@Test func tinySegmentShrinksFadeToHalfItsDuration() {
    // 8ms middle segment: fade clamps to 4ms so ramps can't overlap.
    let ramps = CompositionPlanner.fadeRamps(
        segments: [segment(0, 1), segment(1, 1.008), segment(1.008, 2)])
    let inRamp = ramps[1]
    let outRamp = ramps[2]
    #expect(CMTimeGetSeconds(inRamp.timeRange.duration) <= 0.0041)
    #expect(CMTimeGetSeconds(outRamp.timeRange.duration) <= 0.0041)
    #expect(inRamp.timeRange.end <= outRamp.timeRange.start)
}

// MARK: - PlayableRange from the resolver (N1 delta #1)

@Test func playableRangeFromResolvedRange() {
    let resolved = CFDomainResolvedRangeFixture()
    let url = URL(fileURLWithPath: "/tmp/x.mov")
    let range = PlayableRange(resolved: resolved, url: url)
    #expect(range.url == url)
    #expect(range.startSec == 3.25)
    #expect(range.endSec == 7.5)
}
