import AVFoundation
import CoreMedia
import Foundation
import Testing
@testable import CFMedia

/// PlayerEngine contract tests (cold-review finding 4): state-shaped
/// assertions against tiny fixtures — no hardware-timing claims (those are
/// the N2 harness's job). Everything here must hold from N3 on without a
/// human watching a window.

@MainActor
@Test func loadExposesBuiltSegmentsAndDuration() async throws {
    let a = try await TestFixtures.shared.url(for: TinySpec.h264)
    let engine = PlayerEngine()
    try await engine.load(
        ranges: [
            PlayableRange(url: a, startSec: 0.25, endSec: 1.0),
            PlayableRange(url: a, startSec: 1.25, endSec: 1.75),
        ],
        smoothCutAudio: true)
    let built = try #require(engine.built)
    #expect(built.segments.count == 2)
    #expect(abs(engine.durationSec - 1.25) < 0.01)
    #expect(engine.player.currentItem != nil)
}

@MainActor
@Test func loadPreservesPlayingStateAcrossSwap() async throws {
    let a = try await TestFixtures.shared.url(for: TinySpec.h264)
    let engine = PlayerEngine()
    let ranges = [PlayableRange(url: a, startSec: 0, endSec: 1.5)]
    try await engine.load(ranges: ranges, smoothCutAudio: true)
    engine.player.isMuted = true
    engine.play()
    #expect(engine.isPlaying)
    try await engine.load(ranges: ranges, smoothCutAudio: true, at: 0.5)
    #expect(engine.isPlaying)
    engine.pause()
    try await engine.load(ranges: ranges, smoothCutAudio: true, at: 0.25)
    #expect(!engine.isPlaying)
}

@MainActor
@Test func staleLoadNeverBeatsANewerLoad() async throws {
    // Finding 3: two overlapping loads (each suspends in the builder) must
    // resolve to the load that STARTED last, regardless of completion
    // order. Child-task start order isn't declaration order, so gate the
    // second load on the first having taken its generation ticket.
    let a = try await TestFixtures.shared.url(for: TinySpec.h264)
    let b = try await TestFixtures.shared.url(for: TinySpec.h264B)
    let engine = PlayerEngine()
    let first = Task {
        try await engine.load(
            ranges: [PlayableRange(url: a, startSec: 0, endSec: 1.0)], smoothCutAudio: true)
    }
    while engine.loadGeneration < 1 { await Task.yield() }
    let second = Task {
        try await engine.load(
            ranges: [PlayableRange(url: b, startSec: 0, endSec: 0.5)], smoothCutAudio: true)
    }
    try await first.value
    try await second.value
    let built = try #require(engine.built)
    #expect(built.segments.count == 1)
    #expect(built.segments[0].range.url == b)
}

@MainActor
@Test func loopWindowSurvivesReloadAndRearms() async throws {
    // The re-arm-after-swap contract: boundary observers die with the
    // item; a reload must leave the loop armed on the new item.
    let a = try await TestFixtures.shared.url(for: TinySpec.h264)
    let engine = PlayerEngine()
    let ranges = [PlayableRange(url: a, startSec: 0, endSec: 1.5)]
    try await engine.load(ranges: ranges, smoothCutAudio: true)
    #expect(!engine.isLoopArmed)
    engine.loop(windowStartSec: 0.2, windowEndSec: 0.8)
    #expect(engine.isLoopArmed)
    try await engine.load(ranges: ranges, smoothCutAudio: true, at: 0.2)
    #expect(engine.isLoopArmed)
}

@MainActor
@Test func clearLoopDisarmsTheObserver() async throws {
    let a = try await TestFixtures.shared.url(for: TinySpec.h264)
    let engine = PlayerEngine()
    try await engine.load(
        ranges: [PlayableRange(url: a, startSec: 0, endSec: 1.5)], smoothCutAudio: true)
    engine.loop(windowStartSec: 0.2, windowEndSec: 0.8)
    #expect(engine.isLoopArmed)
    engine.clearLoop()
    #expect(!engine.isLoopArmed)
}
