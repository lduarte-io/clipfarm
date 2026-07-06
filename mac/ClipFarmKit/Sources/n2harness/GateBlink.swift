import AVFoundation
import CFMedia
import CoreMedia
import Foundation
import QuartzCore

/// Gate 2 — swap-blink: 100 edit→rebuild→pre-seek→swap cycles, A/B'd
/// against mutate-in-place. Blink (programmatic definition, PROVISIONAL 2):
/// a black frame delivered within 500 ms after the edit, or a delivery
/// stall > 250 ms while rate == 1. Gate: zero blinks on the winning
/// strategy — the winner becomes the PlayerEngine contract.
@MainActor
func runBlink(env: HarnessEnv, cycles: Int) async throws {
    let file = env.footageFile("btc.0.0.mov")
    func makeRanges(_ edit: Int) -> [PlayableRange] {
        // The middle range's end breathes by ±1 frame per edit — the
        // smallest realistic trim-nudge edit.
        let nudge = Double(edit % 2) * (1.0 / 30.0)
        return [
            PlayableRange(url: file, startSec: 10.37, endSec: 14.21),
            PlayableRange(url: file, startSec: 30.11, endSec: 33.97 + nudge),
            PlayableRange(url: file, startSec: 50.23, endSec: 54.02),
        ]
    }

    // --- Strategy A: rebuild + pre-seek + swap (the engine contract) ---
    let engine = PlayerEngine()
    var currentTap: FrameTap?
    var previousTap: FrameTap?
    engine.itemConfigurator = { item in
        previousTap = currentTap
        let tap = FrameTap(item: item, decode: false)
        tap.start()
        currentTap = tap
    }

    try await engine.load(ranges: makeRanges(0))
    engine.player.isMuted = true
    engine.play()
    try await Task.sleep(for: .seconds(1))

    var swapGapsMs: [Double] = []
    var blinksA = 0
    var blackA = 0
    for cycle in 1...cycles {
        let position = engine.currentTimeSec
        let lastBefore = currentTap?.snapshot().last
        try await engine.load(ranges: makeRanges(cycle), at: min(position, 9.0))
        previousTap?.stop()
        guard let tap = currentTap else { continue }
        let first = await awaitSample(tap, timeoutSec: 2.0) { _ in true }
        if let first, let lastBefore {
            let gap = first.hostTime - lastBefore.hostTime
            swapGapsMs.append(gap)
            let early = tap.snapshot().prefix(15)
            let sawBlack = early.contains { $0.isBlack && $0.hostTime - first.hostTime < 0.5 }
            if sawBlack { blackA += 1 }
            if gap > 0.25 || sawBlack { blinksA += 1 }
        } else {
            blinksA += 1
            swapGapsMs.append(2.0)
        }
        // Let playback breathe between edits (realistic nudge cadence).
        try await Task.sleep(for: .milliseconds(150))
        if engine.currentTimeSec >= 10.5 {
            await engine.seek(toCompositionSeconds: 1.0)
        }
    }
    currentTap?.stop()
    engine.pause()

    // --- Strategy B: mutate the live composition in place (the designed
    // fallback — §2.5 rule 4) ---
    let cache = AssetCache()
    let loaded = try await cache.loaded(for: file)
    guard let videoTrack = loaded.videoTrack, let audioTrack = loaded.audioTrack else {
        throw HarnessError.internalFailure("btc.0.0 tracks missing")
    }
    let mutable = AVMutableComposition()
    let mv = mutable.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid)!
    let ma = mutable.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)!
    func rebuildInPlace(_ edit: Int) throws {
        mv.removeTimeRange(CMTimeRange(start: .zero, duration: mutable.duration))
        ma.removeTimeRange(CMTimeRange(start: .zero, duration: mutable.duration))
        var cursor = CMTime.zero
        for range in makeRanges(edit) {
            let tr = CMTimeRange(
                start: MediaTime.time(range.startSec), end: MediaTime.time(range.endSec))
            try mv.insertTimeRange(tr, of: videoTrack, at: cursor)
            try ma.insertTimeRange(tr, of: audioTrack, at: cursor)
            cursor = cursor + tr.duration
        }
    }
    try rebuildInPlace(0)
    let player = AVPlayer()
    player.automaticallyWaitsToMinimizeStalling = false
    player.isMuted = true
    let item = AVPlayerItem(asset: mutable)
    let tapB = FrameTap(item: item, decode: false)
    tapB.start()
    player.replaceCurrentItem(with: item)
    player.play()
    try await Task.sleep(for: .seconds(1))

    var mutateGapsMs: [Double] = []
    var blinksB = 0
    var blackB = 0
    for cycle in 1...cycles {
        let before = tapB.snapshot().last
        let t0 = CACurrentMediaTime()
        try rebuildInPlace(cycle)
        let first = await awaitSample(tapB, timeoutSec: 2.0) { $0.hostTime > t0 }
        if let first, let before {
            let gap = first.hostTime - before.hostTime
            mutateGapsMs.append(gap)
            let post = tapB.snapshot().filter { $0.hostTime > t0 }.prefix(15)
            let sawBlack = post.contains { $0.isBlack && $0.hostTime - first.hostTime < 0.5 }
            if sawBlack { blackB += 1 }
            if gap > 0.25 || sawBlack { blinksB += 1 }
        } else {
            blinksB += 1
            mutateGapsMs.append(2.0)
        }
        try await Task.sleep(for: .milliseconds(150))
        if item.currentTime().seconds >= 10.5 {
            await player.seek(to: MediaTime.time(1.0), toleranceBefore: .zero, toleranceAfter: .zero)
        }
    }
    tapB.stop()
    player.pause()

    var report: [String] = []
    report.append("**blink** — \(cycles) edit cycles per strategy (playing, muted)")
    report.append("- A rebuild+pre-seek+swap: blinks=\(blinksA) (black=\(blackA))  delivery gap across swap: \(Stats.summary(swapGapsMs))")
    report.append("- B mutate-in-place:       blinks=\(blinksB) (black=\(blackB))  delivery gap across edit: \(Stats.summary(mutateGapsMs))")
    let winner = blinksA <= blinksB ? "A (rebuild+swap — the §2.5 rule 4 default)" : "B (mutate-in-place — the designed fallback)"
    report.append("- winner: \(winner)")
    report.append("- GATE (zero blinks on winner): \(min(blinksA, blinksB) == 0 ? "PASS" : "FAIL")")
    report.append("- note: programmatic blink = decode-level black frame or >250ms delivery stall; layer-level confirmation is Lillian's watch-session demo (PROVISIONAL 2)")
    env.report("blink", report)
}
