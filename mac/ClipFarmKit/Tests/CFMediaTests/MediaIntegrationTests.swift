import AVFoundation
import CFMediaTestSupport
import CoreMedia
import Foundation
import Testing
@testable import CFMedia

/// Probe + builder against real (synthetic) media. Hardware-timing gates
/// live in the N2 harness, not here.

// MARK: - MetadataProbe (D17)

@Test func probeReadsH264Fixture() async throws {
    let url = try await TestFixtures.shared.url(for: TinySpec.h264)
    let meta = try await MetadataProbe.probe(url: url)
    let video = try #require(meta.video)
    #expect(video.naturalSize == CGSize(width: 160, height: 96))
    #expect(video.preferredTransform == .identity)
    #expect(video.codec == "avc1")
    #expect(video.isHDR == false)
    #expect(meta.hasAudio)
    #expect(abs(MediaTime.seconds(meta.duration) - 2.0) < 0.05)
    // Real sample timing, not the average rate.
    #expect(abs(MediaTime.seconds(video.minFrameDuration) - 1.0 / 30.0) < 0.001)
}

@Test func probeDetectsPortraitTransform() async throws {
    let url = try await TestFixtures.shared.url(for: TinySpec.portrait)
    let meta = try await MetadataProbe.probe(url: url)
    let video = try #require(meta.video)
    #expect(video.preferredTransform != .identity)
    #expect(video.orientedSize == CGSize(width: 96, height: 160))
}

@Test func probeDetectsHLGAsHDR() async throws {
    let url = try await TestFixtures.shared.url(for: TinySpec.hlg)
    let meta = try await MetadataProbe.probe(url: url)
    let video = try #require(meta.video)
    #expect(video.isHDR)
    #expect(video.transferFunction == String(kCMFormatDescriptionTransferFunction_ITU_R_2100_HLG))
    #expect(video.codec == "hvc1")
}

@Test func probeOfMissingFileThrows() async throws {
    await #expect(throws: (any Error).self) {
        _ = try await MetadataProbe.probe(
            url: URL(fileURLWithPath: "/nonexistent/never.mov"))
    }
}

// MARK: - Fixture self-check (the gate math stands on this)

@Test func fixtureFramesAreSelfIdentifying() async throws {
    let url = try await TestFixtures.shared.url(for: TinySpec.h264)
    let asset = AVURLAsset(url: url)
    let track = try #require(try await asset.loadTracks(withMediaType: .video).first)
    let reader = try AVAssetReader(asset: asset)
    let output = AVAssetReaderTrackOutput(
        track: track,
        outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
    reader.add(output)
    #expect(reader.startReading())
    // Anchor each decoded index to the sample's OWN presentation time —
    // H.264 B-frame reordering means emission order need not be 0,1,2,…
    for _ in 0..<10 {
        let sample = try #require(output.copyNextSampleBuffer())
        let pixelBuffer = try #require(CMSampleBufferGetImageBuffer(sample))
        let pts = CMSampleBufferGetPresentationTimeStamp(sample).seconds
        let expected = Int((pts * 30.0).rounded())
        #expect(PixelProbe.frameIndex(in: pixelBuffer) == expected)
        #expect(!PixelProbe.isBlack(pixelBuffer))
    }
    reader.cancelReading()
}

// MARK: - CompositionBuilder (§2.5)

private func builder() -> CompositionBuilder {
    CompositionBuilder(assetCache: AssetCache())
}

@Test func singleTrackPairAndBackToBackInsertion() async throws {
    let a = try await TestFixtures.shared.url(for: TinySpec.h264)
    let b = try await TestFixtures.shared.url(for: TinySpec.h264B)
    let result = try await builder().build(ranges: [
        PlayableRange(url: a, startSec: 0.5, endSec: 1.5),
        PlayableRange(url: b, startSec: 0.0, endSec: 0.5),
    ])
    // Rule 1: exactly one video + one audio track.
    #expect(result.composition.tracks(withMediaType: .video).count == 1)
    #expect(result.composition.tracks(withMediaType: .audio).count == 1)
    // Back-to-back mapping.
    #expect(result.segments.count == 2)
    #expect(result.segments[0].compositionStart == .zero)
    #expect(result.segments[0].duration == MediaTime.time(1.0))
    #expect(result.segments[1].compositionStart == MediaTime.time(1.0))
    #expect(abs(MediaTime.seconds(result.duration) - 1.5) < 0.001)
    // Uniform geometry, uniform SDR → bare composition (Lossless door open).
    #expect(result.videoComposition == nil)
}

@Test func requestBeyondSourceEndIsClamped() async throws {
    let a = try await TestFixtures.shared.url(for: TinySpec.h264)
    let result = try await builder().build(ranges: [
        PlayableRange(url: a, startSec: 0.5, endSec: 100.0)
    ])
    // 2s fixture → clamped to ~1.5s inserted.
    let duration = MediaTime.seconds(result.segments[0].duration)
    #expect(abs(duration - 1.5) < 0.05)
}

@Test func clampToNothingThrows() async throws {
    let a = try await TestFixtures.shared.url(for: TinySpec.h264)
    await #expect(throws: CompositionBuildError.self) {
        _ = try await builder().build(ranges: [
            PlayableRange(url: a, startSec: 50.0, endSec: 60.0)
        ])
    }
}

@Test func emptyRangeListThrows() async throws {
    await #expect(throws: CompositionBuildError.emptyRangeList) {
        _ = try await builder().build(ranges: [])
    }
}

@Test func mixedRotationAttachesVideoComposition() async throws {
    let landscape = try await TestFixtures.shared.url(for: TinySpec.h264)
    let portrait = try await TestFixtures.shared.url(for: TinySpec.portrait)
    let result = try await builder().build(ranges: [
        PlayableRange(url: landscape, startSec: 0, endSec: 1),
        PlayableRange(url: portrait, startSec: 0, endSec: 1),
    ])
    let vc = try #require(result.videoComposition)
    // D32: canvas defaults to the first segment's oriented size.
    #expect(vc.renderSize == CGSize(width: 160, height: 96))
    #expect(vc.instructions.count == 2)
    // Uniform SDR: no color override needed.
    #expect(vc.colorPrimaries == nil)
}

@Test func mixedDynamicRangeEnforcesSDRColorProperties() async throws {
    let sdr = try await TestFixtures.shared.url(for: TinySpec.h264)
    let hdr = try await TestFixtures.shared.url(for: TinySpec.hlg)
    let result = try await builder().build(ranges: [
        PlayableRange(url: sdr, startSec: 0, endSec: 1),
        PlayableRange(url: hdr, startSec: 0, endSec: 1),
    ])
    // Same size/transform — geometry is uniform; color mixing alone must
    // force the videoComposition (D29: SDR default enforced, not assumed).
    let vc = try #require(result.videoComposition)
    #expect(vc.colorPrimaries == String(kCMFormatDescriptionColorPrimaries_ITU_R_709_2))
    #expect(vc.colorTransferFunction == String(kCMFormatDescriptionTransferFunction_ITU_R_709_2))
    #expect(vc.colorYCbCrMatrix == String(kCMFormatDescriptionYCbCrMatrix_ITU_R_709_2))
}

@Test func smoothCutAudioAttachesMixOnlyAtInternalBoundaries() async throws {
    let a = try await TestFixtures.shared.url(for: TinySpec.h264)
    let single = try await builder().build(ranges: [
        PlayableRange(url: a, startSec: 0, endSec: 1)
    ])
    // No internal boundary → no mix.
    #expect(single.audioMix == nil)

    let double = try await builder().build(ranges: [
        PlayableRange(url: a, startSec: 0, endSec: 1),
        PlayableRange(url: a, startSec: 1, endSec: 2),
    ])
    #expect(double.audioMix != nil)

    let fadesOff = try await builder().build(
        ranges: [
            PlayableRange(url: a, startSec: 0, endSec: 1),
            PlayableRange(url: a, startSec: 1, endSec: 2),
        ],
        smoothCutAudio: false
    )
    #expect(fadesOff.audioMix == nil)
}

@Test func footageOnlySourceGetsEmptyAudioRange() async throws {
    let muted = try await TestFixtures.shared.url(for: TinySpec.videoOnly)
    let result = try await builder().build(ranges: [
        PlayableRange(url: muted, startSec: 0, endSec: 1)
    ])
    // A/V alignment preserved via silence; total duration unchanged.
    #expect(abs(MediaTime.seconds(result.duration) - 1.0) < 0.001)
    #expect(result.composition.tracks(withMediaType: .audio).count == 1)
}

@Test func warmCacheRebuildIsFast() async throws {
    // The §6 budget (<10ms @ 50 clips) is measured by the harness on real
    // footage; this only pins "second build is edit-list-cheap" as a
    // regression tripwire, with slack for CI noise.
    let cache = AssetCache()
    let b = CompositionBuilder(assetCache: cache)
    let a = try await TestFixtures.shared.url(for: TinySpec.h264)
    let ranges = (0..<50).map { i in
        PlayableRange(url: a, startSec: Double(i % 3) * 0.5, endSec: Double(i % 3) * 0.5 + 0.4)
    }
    _ = try await b.build(ranges: ranges)  // warm
    let start = ContinuousClock.now
    _ = try await b.build(ranges: ranges)
    let elapsed = ContinuousClock.now - start
    #expect(elapsed < .milliseconds(100))
}
