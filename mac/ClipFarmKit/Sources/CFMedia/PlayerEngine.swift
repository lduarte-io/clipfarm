import AVFoundation
import CoreMedia
import Foundation
import Observation

/// §2.5: one engine instance app-wide, driving one persistent `AVPlayer`
/// in the preview surface (D30). Explicitly `@MainActor` (class-level, not
/// a target-default flip): the engine is UI-adjacent state — views observe
/// it, the player surface renders it — and AVPlayer manipulation stays on
/// one actor by design.
///
/// Swap discipline (rule 4): every edit builds a fresh composition
/// snapshot; the new item is pre-seeked with zero tolerance and the seek
/// AWAITED before `replaceCurrentItem` — and every observer is re-armed
/// after every swap (boundary observers do not survive item replacement —
/// the re-arm rule is part of this engine's contract).
///
/// Loop mode (D13): a boundary time observer at the window end re-seeks to
/// the window start with zero tolerance (NOT AVPlayerLooper — its range is
/// fixed at creation; trim mode changes the window on every keystroke).
/// A periodic observer provides belt-and-suspenders overshoot recovery in
/// case a boundary fire is ever missed.
@MainActor
@Observable
public final class PlayerEngine {
    public let player = AVPlayer()

    /// For the scrubber only — periodic-observer resolution, not for edits.
    public private(set) var currentTimeSec: Double = 0
    public private(set) var built: CompositionBuildResult?

    /// Called with each freshly-built item BEFORE it becomes current — the
    /// attach point for video outputs / KVO (player surface at N4; the N2
    /// harness's frame-delivery instrumentation).
    @ObservationIgnored
    public var itemConfigurator: ((AVPlayerItem) -> Void)?

    public var isPlaying: Bool { player.rate != 0 }
    public var durationSec: Double {
        built.map { MediaTime.seconds($0.duration) } ?? 0
    }

    private let assetCache: AssetCache
    private let builder: CompositionBuilder
    private var boundaryObserver: Any?
    private var periodicObserver: Any?
    private var loopWindow: CMTimeRange?
    private var loopSeekInFlight = false

    public init(assetCache: AssetCache = AssetCache()) {
        self.assetCache = assetCache
        self.builder = CompositionBuilder(assetCache: assetCache)
        // Rule 5 tail: local files never stall — don't let the player
        // trade latency for buffer depth.
        player.automaticallyWaitsToMinimizeStalling = false
        installPeriodicObserver()
    }

    // MARK: - Loading / editing

    /// Build → pre-seek (awaited, zero tolerance) → swap → re-arm.
    /// Also the edit path: callers re-`load` with the changed ranges and
    /// the current position; playback state (playing/paused) is preserved.
    public func load(
        ranges: [PlayableRange],
        smoothCutAudio: Bool = true,
        at seconds: Double = 0
    ) async throws {
        let result = try await builder.build(ranges: ranges, smoothCutAudio: smoothCutAudio)
        let item = result.makePlayerItem()
        itemConfigurator?(item)

        let target = MediaTime.time(seconds)
        if target > .zero {
            await item.seek(to: target, toleranceBefore: .zero, toleranceAfter: .zero)
        }

        let wasPlaying = isPlaying
        player.replaceCurrentItem(with: item)
        built = result
        rearmBoundaryObserver()
        if wasPlaying { player.play() }
    }

    // MARK: - Transport

    public func play() { player.play() }
    public func pause() { player.pause() }

    /// Zero-tolerance seek — lands exactly on the requested composition
    /// time, never the nearest keyframe.
    public func seek(toCompositionSeconds seconds: Double) async {
        await player.seek(
            to: MediaTime.time(seconds), toleranceBefore: .zero, toleranceAfter: .zero)
    }

    /// Frame stepping across the whole composition (seams included).
    /// Stepping is a paused-transport operation; the engine pauses first.
    public func step(frames: Int) {
        player.pause()
        player.currentItem?.step(byCount: frames)
    }

    // MARK: - Trim-mode loop (D13)

    public func loop(windowStartSec: Double, windowEndSec: Double) {
        loopWindow = CMTimeRange(
            start: MediaTime.time(windowStartSec),
            end: MediaTime.time(windowEndSec)
        )
        rearmBoundaryObserver()
    }

    public func clearLoop() {
        loopWindow = nil
        rearmBoundaryObserver()
    }

    /// Boundary observers die with the item — call after EVERY swap and
    /// every window change. (The engine does; this is exposed for the
    /// contract's sake and the harness's instrumentation.)
    public func rearmBoundaryObserver() {
        if let boundaryObserver {
            player.removeTimeObserver(boundaryObserver)
            self.boundaryObserver = nil
        }
        guard let loopWindow else { return }
        boundaryObserver = player.addBoundaryTimeObserver(
            forTimes: [NSValue(time: loopWindow.end)],
            queue: .main
        ) { [weak self] in
            MainActor.assumeIsolated {
                self?.loopDidHitEnd()
            }
        }
    }

    private func loopDidHitEnd() {
        guard let loopWindow, !loopSeekInFlight else { return }
        loopSeekInFlight = true
        let wasPlaying = isPlaying
        player.seek(
            to: loopWindow.start, toleranceBefore: .zero, toleranceAfter: .zero
        ) { [weak self] _ in
            DispatchQueue.main.async {
                guard let self else { return }
                self.loopSeekInFlight = false
                if wasPlaying { self.player.play() }
            }
        }
    }

    private func installPeriodicObserver() {
        periodicObserver = player.addPeriodicTimeObserver(
            forInterval: CMTime(value: 20, timescale: 600),  // 30 Hz
            queue: .main
        ) { [weak self] time in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.currentTimeSec = MediaTime.seconds(time)
                // Belt-and-suspenders: a missed boundary fire may leave the
                // playhead past the loop window — recover.
                if let loopWindow = self.loopWindow,
                   !self.loopSeekInFlight,
                   time > loopWindow.end + CMTime(value: 30, timescale: 600) {
                    self.loopDidHitEnd()
                }
            }
        }
    }

    // SE-0371 isolated deinit: observer teardown must touch MainActor state.
    isolated deinit {
        if let boundaryObserver { player.removeTimeObserver(boundaryObserver) }
        if let periodicObserver { player.removeTimeObserver(periodicObserver) }
    }
}
