import AVFoundation
import CFMedia
import CoreMedia
import Foundation

/// Gate 8 — micro-fades: audibly kill cut pops without softening speech
/// onsets. Programmatic legs (PROVISIONAL 2): offline audio render of the
/// SAME composition with fades on/off — (a) sample discontinuity at a
/// mid-sine splice must collapse, (b) a burst onset placed exactly at a
/// cut must reach full level within the 10ms fade window. Audible legs:
/// WAV pairs (fixture + real speech from btc.0.4) written for the watch
/// session.
@MainActor
func runFades(env: HarnessEnv) async throws {
    let url = try await env.ensureFixture(FixtureSet.bursts)
    // 440Hz bed cut mid-phase at 3.113s → 6.001s (deliberate phase jump);
    // second boundary lands exactly on a burst onset (bursts start at
    // every whole 2s; 10.0 is one).
    let ranges = [
        PlayableRange(url: url, startSec: 0.5, endSec: 3.113),
        PlayableRange(url: url, startSec: 6.001, endSec: 8.5),
        PlayableRange(url: url, startSec: 10.0, endSec: 12.0),
    ]
    let builder = CompositionBuilder(assetCache: AssetCache())
    let fadesOn = try await builder.build(ranges: ranges, smoothCutAudio: true)
    let fadesOff = try await builder.build(ranges: ranges, smoothCutAudio: false)

    let samplesOn = try await renderAudio(built: fadesOn)
    let samplesOff = try await renderAudio(built: fadesOff)
    let sampleRate = 48_000.0

    let seam1 = MediaTime.seconds(fadesOn.segments[1].compositionStart)  // mid-sine splice
    let seam2 = MediaTime.seconds(fadesOn.segments[2].compositionStart)  // burst onset

    func maxDelta(_ samples: [Float], around timeSec: Double, windowMs: Double = 10) -> Double {
        let center = Int(timeSec * sampleRate)
        let half = Int(windowMs / 1000 * sampleRate)
        let lo = max(1, center - half)
        let hi = min(samples.count - 1, center + half)
        guard lo < hi else { return .nan }
        var best = 0.0
        for i in lo...hi {
            best = max(best, abs(Double(samples[i]) - Double(samples[i - 1])))
        }
        return best
    }
    func rms(_ samples: [Float], from: Double, durationMs: Double) -> Double {
        let lo = Int(from * sampleRate)
        let hi = min(samples.count, lo + Int(durationMs / 1000 * sampleRate))
        guard lo < hi else { return .nan }
        var sum = 0.0
        for i in lo..<hi { sum += Double(samples[i]) * Double(samples[i]) }
        return (sum / Double(hi - lo)).squareRoot()
    }

    // (a) Pop: discontinuity at the mid-sine splice.
    let popOff = maxDelta(samplesOff, around: seam1)
    let popOn = maxDelta(samplesOn, around: seam1)
    // A 440Hz/0.55-amplitude sine moves ≤ 2π·440/48000·0.55 ≈ 0.032 per
    // sample — anything well above that at the splice is the pop.
    let popKilled = popOn < max(0.06, popOff * 0.25)

    // (b) Onset: burst at seam2 must reach ≥90% of its steady RMS right
    // after the fade window (compare 12–24ms post-cut vs 50–100ms).
    let steadyOn = rms(samplesOn, from: seam2 + 0.050, durationMs: 50)
    let earlyOn = rms(samplesOn, from: seam2 + 0.012, durationMs: 12)
    let onsetPreserved = earlyOn >= steadyOn * 0.9
    // And the fade window itself must ramp (not silence-then-jump).
    let inFadeOn = rms(samplesOn, from: seam2, durationMs: 10)
    let inFadeOff = rms(samplesOff, from: seam2, durationMs: 10)

    // Audible artifacts.
    try writeWAV(samplesOn, sampleRate: Int(sampleRate),
                 to: env.workdir.appendingPathComponent("audio/fixture-fades-on.wav"))
    try writeWAV(samplesOff, sampleRate: Int(sampleRate),
                 to: env.workdir.appendingPathComponent("audio/fixture-fades-off.wav"))

    // Real-speech A/B from btc.0.4 (mid-speech cuts, for listening).
    let speech = env.footageFile("btc.0.4.mov")
    let speechRanges = [
        PlayableRange(url: speech, startSec: 63.4, endSec: 66.9),
        PlayableRange(url: speech, startSec: 121.2, endSec: 124.6),
        PlayableRange(url: speech, startSec: 245.7, endSec: 249.1),
    ]
    let speechOn = try await renderAudio(built: builder.build(ranges: speechRanges, smoothCutAudio: true))
    let speechOff = try await renderAudio(built: builder.build(ranges: speechRanges, smoothCutAudio: false))
    try writeWAV(speechOn, sampleRate: Int(sampleRate),
                 to: env.workdir.appendingPathComponent("audio/speech-fades-on.wav"))
    try writeWAV(speechOff, sampleRate: Int(sampleRate),
                 to: env.workdir.appendingPathComponent("audio/speech-fades-off.wav"))

    var report: [String] = ["**fades** — ~10ms AVAudioMix ramps at cut boundaries (D31), offline-rendered"]
    report.append("- pop at mid-sine splice: fades OFF max Δ/sample = \(fmt(popOff, 3)), fades ON = \(fmt(popOn, 3)) → \(popKilled ? "killed" : "NOT killed")")
    report.append("- burst-onset at cut: RMS 12–24ms post-cut = \(fmt(earlyOn, 3)) vs steady = \(fmt(steadyOn, 3)) (ratio \(fmt(earlyOn / steadyOn, 2))) → \(onsetPreserved ? "onset preserved" : "SOFTENED")")
    report.append("- in-fade-window RMS (0–10ms): on=\(fmt(inFadeOn, 3)) off=\(fmt(inFadeOff, 3)) (ramp expected to sit below the off value)")
    report.append("- GATE: \(popKilled && onsetPreserved ? "PASS" : "FAIL") (programmatic; audible A/B at audio/*.wav for the watch session)")
    env.report("fades", report)
}

/// Offline-render a composition's audio (honoring its audioMix) to mono
/// Float32 48kHz.
func renderAudio(built: CompositionBuildResult) async throws -> [Float] {
    let reader = try AVAssetReader(asset: built.composition)
    let audioTracks = try await built.composition.loadTracks(withMediaType: .audio)
    let output = AVAssetReaderAudioMixOutput(
        audioTracks: audioTracks,
        audioSettings: [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: 48_000,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 32,
            AVLinearPCMIsFloatKey: true,
            AVLinearPCMIsNonInterleaved: false,
        ])
    output.audioMix = built.audioMix
    reader.add(output)
    guard reader.startReading() else {
        throw HarnessError.internalFailure("audio reader failed: \(String(describing: reader.error))")
    }
    var samples: [Float] = []
    while let sampleBuffer = output.copyNextSampleBuffer() {
        guard let blockBuffer = CMSampleBufferGetDataBuffer(sampleBuffer) else { continue }
        let length = CMBlockBufferGetDataLength(blockBuffer)
        var data = [Float](repeating: 0, count: length / 4)
        _ = data.withUnsafeMutableBytes {
            CMBlockBufferCopyDataBytes(
                blockBuffer, atOffset: 0, dataLength: length, destination: $0.baseAddress!)
        }
        samples.append(contentsOf: data)
    }
    reader.cancelReading()
    return samples
}

/// Minimal 16-bit PCM WAV writer for the audible artifacts.
func writeWAV(_ samples: [Float], sampleRate: Int, to url: URL) throws {
    var data = Data()
    let byteCount = samples.count * 2
    func append<T>(_ value: T) {
        withUnsafeBytes(of: value) { data.append(contentsOf: $0) }
    }
    data.append(contentsOf: "RIFF".utf8)
    append(UInt32(36 + byteCount))
    data.append(contentsOf: "WAVE".utf8)
    data.append(contentsOf: "fmt ".utf8)
    append(UInt32(16))
    append(UInt16(1))  // PCM
    append(UInt16(1))  // mono
    append(UInt32(sampleRate))
    append(UInt32(sampleRate * 2))
    append(UInt16(2))
    append(UInt16(16))
    data.append(contentsOf: "data".utf8)
    append(UInt32(byteCount))
    for sample in samples {
        append(Int16(max(-1, min(1, sample)) * 32767))
    }
    try data.write(to: url)
}
