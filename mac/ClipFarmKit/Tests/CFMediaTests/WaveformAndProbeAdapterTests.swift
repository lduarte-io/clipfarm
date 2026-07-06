import CFDomain
import CFMediaTestSupport
import Foundation
import Testing
@testable import CFMedia

/// N3's CFMedia additions: the probe→ingest adapter (D17 fields) and the
/// WaveformService (AVAssetReader + vDSP peaks, binary cache).

// MARK: - Probe adapter

@Test func probeAdapterMapsIngestFields() async throws {
    let url = try await TestFixtures.shared.url(for: TinySpec.h264)
    let metadata = try await MetadataProbe.probe(url: url)
    let info = metadata.probedSourceInfo

    #expect(info.durationSec != nil)
    #expect(abs((info.durationSec ?? 0) - 2.0) < 0.1)
    // Display fps = nominalFrameRate (N2 delta), 30fps CFR fixture.
    #expect(abs((info.fps ?? 0) - 30.0) < 0.5)
    #expect(info.isHDR == false)
    #expect(info.naturalWidth == 160)
    #expect(info.naturalHeight == 96)
}

@Test func probeAdapterFlagsHLGAsHDR() async throws {
    let url = try await TestFixtures.shared.url(for: TinySpec.hlg)
    let info = try await MetadataProbe.probe(url: url).probedSourceInfo
    #expect(info.isHDR == true)
}

// MARK: - Waveform

private func makeScratchDirectory() throws -> URL {
    let url = FileManager.default.temporaryDirectory
        .appendingPathComponent("cfmedia-waveform-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
}

/// 4s mono fixture: 440 Hz bed at amplitude 0.15 with 0.7-amplitude 250 ms
/// bursts starting at every whole 2 s (t=0 and t=2).
private let burstSpec = MediaFixtureSpec(
    name: "waveform-bursts", codec: .h264, width: 160, height: 96,
    fps: 30, durationSec: 4.0, grayLevel: 118, audio: .sineWithBursts)

@Test func waveformBucketsTrackBurstsAndBed() async throws {
    let scratch = try makeScratchDirectory()
    defer { try? FileManager.default.removeItem(at: scratch) }
    let source = try await TestFixtures.shared.url(for: burstSpec)

    let service = WaveformService(cacheDirectory: scratch)
    let cacheURL = try await service.generateIfNeeded(forSourceID: "1", sourceURL: source)
    let waveform = try WaveformService.read(from: cacheURL)

    #expect(waveform.bucketsPerSecond == 100)
    // ~4s * 100 buckets/sec (allow codec/priming edge slack).
    #expect(abs(waveform.peaks.count - 400) <= 2)

    // A bucket inside the t=2 burst is loud; a bed-only bucket is quiet.
    let burstBucket = waveform.peaks[210]
    let bedBucket = waveform.peaks[100]
    #expect(burstBucket > 0.5, "burst bucket should carry the 0.7 burst")
    #expect(bedBucket > 0.05 && bedBucket < 0.35, "bed bucket should be sine-only")
}

@Test func waveformCacheRoundTripsAndSkipsIfExists() async throws {
    let scratch = try makeScratchDirectory()
    defer { try? FileManager.default.removeItem(at: scratch) }
    let source = try await TestFixtures.shared.url(for: burstSpec)

    let service = WaveformService(cacheDirectory: scratch)
    let first = try await service.generateIfNeeded(forSourceID: "s", sourceURL: source)
    let firstStamp = try FileManager.default.attributesOfItem(atPath: first.path)[.modificationDate] as? Date

    // Encode/read round-trip is exact.
    let waveform = try WaveformService.read(from: first)
    let reencoded = WaveformService.encode(waveform)
    #expect(try WaveformService.read(from: first) == waveform)
    #expect(reencoded == (try Data(contentsOf: first)))

    // Second call skips the decode entirely (file untouched).
    let second = try await service.generateIfNeeded(forSourceID: "s", sourceURL: source)
    let secondStamp = try FileManager.default.attributesOfItem(atPath: second.path)[.modificationDate] as? Date
    #expect(first == second)
    #expect(firstStamp == secondStamp)

    // A truncated cache (killed run) is treated as absent and regenerated.
    let truncated = try Data(contentsOf: first).prefix(WaveformService.headerSize + 10)
    try truncated.write(to: first)
    #expect(!WaveformService.cacheFileIsValid(at: first))
    let regenerated = try await WaveformService(cacheDirectory: scratch)
        .generateIfNeeded(forSourceID: "s", sourceURL: source)
    #expect(try WaveformService.read(from: regenerated) == waveform)
}

// MARK: - Real-material leg (skips when the inbox lacks the file)

private let realAudioFile = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("ClipFarm/Footage/btc.0.2.mov")

/// Generates a waveform for a real ~3-minute recording (inbox read-only;
/// cache in temp). No timing assertion — the perf number is read from a
/// `swift test -c release` run (N2 delta: debug hot loops are 20–50x
/// slower) and recorded in the phase closeout.
@Test(.enabled(if: FileManager.default.fileExists(atPath: realAudioFile.path)))
func realRecordingWaveformGeneratesAndCounts() async throws {
    let scratch = try makeScratchDirectory()
    defer { try? FileManager.default.removeItem(at: scratch) }

    let clock = ContinuousClock()
    let started = clock.now
    let service = WaveformService(cacheDirectory: scratch)
    let cacheURL = try await service.generateIfNeeded(forSourceID: "real", sourceURL: realAudioFile)
    let elapsed = clock.now - started
    let waveform = try WaveformService.read(from: cacheURL)

    // btc.0.2 is 181.163s -> ~18,116 buckets at 100/sec.
    #expect(abs(waveform.peaks.count - 18_116) < 200)
    #expect(waveform.peaks.max() ?? 0 > 0.1, "speech should register")
    print("WAVEFORM-PERF btc.0.2.mov (181s): \(elapsed) -> \(waveform.peaks.count) buckets")
}

@Test func audioLessSourceGetsAnEmptyWaveformFile() async throws {
    let scratch = try makeScratchDirectory()
    defer { try? FileManager.default.removeItem(at: scratch) }
    let source = try await TestFixtures.shared.url(for: TinySpec.videoOnly)

    let service = WaveformService(cacheDirectory: scratch)
    let cacheURL = try await service.generateIfNeeded(forSourceID: "v", sourceURL: source)
    let waveform = try WaveformService.read(from: cacheURL)
    // Empty (0 buckets) = "no audio", distinct from "not generated yet"
    // (no file) — N11's degrade-gracefully contract.
    #expect(waveform.peaks.isEmpty)
}
