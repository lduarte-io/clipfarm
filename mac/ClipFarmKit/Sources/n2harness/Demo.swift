import AppKit
import AVFoundation
import CFMedia
import CoreMedia
import Foundation
import QuartzCore

/// `swift run n2harness demo [--real] [--selfcheck]` — the watch-session
/// surface: a real window + AVPlayerLayer playing a multi-source assembly
/// through the PlayerEngine. Lillian's plan-level verify is exactly this:
/// "watch a multi-source (camera + iPhone-style) assembly play gapless."
///
/// Default assembly = footage-inbox files interleaved with the HLG-HDR and
/// portrait fixtures (exercises D29 + D32 in one watch); `--real`
/// restricts it to inbox files only. Space = play/pause, R = reload
/// (swap-blink eyeball), L = 1.5s loop at the current position (trim-loop
/// eyeball), Esc/Q = quit.
///
/// Presentation wiring (black-window incident, 2026-07-06): the original
/// demo added the AVPlayerLayer as a sublayer of the view's lazily-created
/// backing layer inside `init` and sized it in `layout()` — two links that
/// can silently no-op (nil layer at init; layout never firing), and the
/// ONLY place in N2 that used the AVPlayerLayer route at all (every gate
/// probe reads AVPlayerItemVideoOutput). Rebuilt on `makeBackingLayer`:
/// the player layer IS the view's backing layer, AppKit sizes it with the
/// view, and there is no manual attach/frame step left to go wrong. A
/// DEMO SELF-CHECK block prints the presentation-path state at 2s/5s
/// (layer readiness, layer size, player rate, item status, decode-level
/// frame flow) so a black screen can be diagnosed from stdout alone;
/// `--selfcheck` auto-quits after ~6s for headless regression runs.
@MainActor
func runDemo(env: HarnessEnv, realOnly: Bool, selfCheck: Bool = false) async throws {
    let probed = await env.probedRealFiles()
    var realRanges: [PlayableRange] = []
    for real in probed {
        let d = real.meta.duration.seconds
        realRanges.append(contentsOf: spreadRanges(
            url: real.url, durationSec: d, count: 2, length: min(5.0, d * 0.3)))
    }
    var ranges: [PlayableRange]
    if realOnly {
        ranges = realRanges
        guard !ranges.isEmpty else {
            throw HarnessError.usage("demo --real needs at least one file in the footage inbox (\(env.footage.path))")
        }
    } else {
        ranges = realRanges
        // Interleave the D32 + D29 fixture material after the first real
        // range (or standalone when the inbox is empty).
        let portrait = PlayableRange(
            url: try await env.ensureFixture(FixtureSet.portrait), startSec: 3.0, endSec: 6.0)
        let hlg = PlayableRange(
            url: try await env.ensureFixture(FixtureSet.hlg), startSec: 8.0, endSec: 11.0)
        ranges.insert(portrait, at: ranges.isEmpty ? 0 : 1)
        ranges.insert(hlg, at: min(3, ranges.count))
        if realRanges.isEmpty {
            print("demo: footage inbox is empty — playing fixture-only assembly (drop files into \(env.footage.path) for the real demo)")
        }
    }
    print("demo assembly (\(ranges.count) ranges):")
    for (i, r) in ranges.enumerated() {
        print("  \(i + 1). \(r.url.lastPathComponent) [\(fmt(r.startSec, 2))–\(fmt(r.endSec, 2))]")
    }

    let app = NSApplication.shared
    app.setActivationPolicy(.regular)

    let engine = PlayerEngine()
    // Decode-level frame flow for the self-check — proves delivery in the
    // demo's exact composition even if the screen looks wrong.
    var tap: FrameTap?
    engine.itemConfigurator = { item in
        tap?.stop()
        let fresh = FrameTap(item: item, decode: false)
        fresh.start()
        tap = fresh
    }
    try await engine.load(ranges: ranges, smoothCutAudio: true)

    let window = NSWindow(
        contentRect: NSRect(x: 0, y: 0, width: 1280, height: 720),
        styleMask: [.titled, .closable, .resizable],
        backing: .buffered, defer: false)
    window.title = "ClipFarm N2 demo — \(realOnly ? "inbox-only" : "inbox + portrait/HDR fixture") assembly (\(ranges.count) ranges)"
    let view = PlayerHostView(engine: engine, ranges: ranges)
    window.contentView = view
    window.center()
    window.makeKeyAndOrderFront(nil)
    window.makeFirstResponder(view)
    app.activate()
    let closeObserver = NotificationCenter.default.addObserver(
        forName: NSWindow.willCloseNotification, object: window, queue: nil
    ) { _ in
        print("demo: window closed — exiting")
        fflush(stdout)
        exit(0)
    }
    defer { NotificationCenter.default.removeObserver(closeObserver) }
    app.finishLaunching()

    engine.play()
    print("demo: playing \(ranges.count) ranges. Space=pause  R=reload(swap)  L=loop-here  Esc/Q=quit  (click the window once if keys don't respond)")
    fflush(stdout)

    // Run-loop integration (black-window incident, root cause 2): this
    // process's async main() executes ON the main dispatch queue, and a
    // nested `app.run()` inside a dispatch callout NEVER drains that
    // queue — so every DispatchQueue.main block, every @MainActor Task
    // (the R-key reload), and the engine's queue-.main observers starve.
    // Instead of app.run(), a hybrid pump: service AppKit events for a
    // slice, then SUSPEND (Task.sleep) so control returns to the main
    // queue's top level and MainActor/dispatch work drains. Everything
    // works: window events, engine observers, concurrency.
    let started = CACurrentMediaTime()
    var nextCheck = 2.0
    let checks: [Double] = [2.0, 5.0]
    var checkIndex = 0
    while true {
        let sliceEnd = Date().addingTimeInterval(0.008)
        while let event = app.nextEvent(
            matching: .any, until: sliceEnd, inMode: .default, dequeue: true) {
            app.sendEvent(event)
        }
        app.updateWindows()
        try? await Task.sleep(for: .milliseconds(4))

        let elapsed = CACurrentMediaTime() - started
        if checkIndex < checks.count, elapsed >= checks[checkIndex] {
            nextCheck = checks[checkIndex]
            checkIndex += 1
            let item = engine.player.currentItem
            let layer = view.presentationLayer
            let frames = tap?.snapshot() ?? []
            print(
                "DEMO SELF-CHECK @\(Int(nextCheck))s: "
                + "rate=\(engine.player.rate) "
                + "itemStatus=\(item.map { String(describing: $0.status.rawValue) } ?? "nil") "
                + "itemError=\(item?.error.map(String.init(describing:)) ?? "nil") "
                + "itemTime=\(fmt(engine.currentTimeSec, 2)) "
                + "layerIsBacking=\(view.layer === layer) "
                + "layerReadyForDisplay=\(layer.isReadyForDisplay) "
                + "layerBounds=\(Int(layer.bounds.width))×\(Int(layer.bounds.height)) "
                + "videoRect=\(Int(layer.videoRect.width))×\(Int(layer.videoRect.height)) "
                + "decodedFrames=\(frames.count) lastFrameItemTime=\(frames.last.map { fmt($0.itemTimeSec, 2) } ?? "—")"
            )
            fflush(stdout)
        }
        if selfCheck, elapsed >= 6.5 {
            print("demo --selfcheck: auto-exit")
            fflush(stdout)
            exit(0)
        }
    }
}

@MainActor
final class PlayerHostView: NSView {
    private let engine: PlayerEngine
    private let ranges: [PlayableRange]
    private let playerLayer: AVPlayerLayer

    /// The AVPlayerLayer for self-check inspection.
    var presentationLayer: AVPlayerLayer { playerLayer }

    init(engine: PlayerEngine, ranges: [PlayableRange]) {
        self.engine = engine
        self.ranges = ranges
        self.playerLayer = AVPlayerLayer(player: engine.player)
        playerLayer.videoGravity = .resizeAspect
        playerLayer.backgroundColor = NSColor.black.cgColor
        super.init(frame: .zero)
        // The player layer IS the backing layer (makeBackingLayer below):
        // AppKit attaches and sizes it with the view — no manual sublayer
        // or frame management to silently no-op.
        wantsLayer = true
    }

    required init?(coder: NSCoder) { fatalError() }

    override func makeBackingLayer() -> CALayer { playerLayer }

    override var acceptsFirstResponder: Bool { true }

    override func keyDown(with event: NSEvent) {
        switch event.charactersIgnoringModifiers?.lowercased() {
        case " ":
            engine.isPlaying ? engine.pause() : engine.play()
        case "r":
            Task { @MainActor in
                let at = engine.currentTimeSec
                try? await engine.load(ranges: ranges, smoothCutAudio: true, at: at)
                engine.play()
            }
        case "l":
            // Clamp inside the composition (finding 10): a window end past
            // the duration never fires and the loop silently no-ops.
            let at = engine.currentTimeSec
            let end = min(at + 0.75, max(0.1, engine.durationSec - 0.05))
            engine.loop(windowStartSec: max(0, end - 1.5), windowEndSec: end)
            engine.play()
        case "q", "\u{1b}":
            NSApplication.shared.terminate(nil)
        default:
            super.keyDown(with: event)
        }
    }
}
