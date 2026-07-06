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
    window.isReleasedWhenClosed = false

    let terminateObserver = NotificationCenter.default.addObserver(
        forName: NSApplication.willTerminateNotification, object: nil, queue: nil
    ) { _ in
        fputs("demo: NSApplication willTerminate\n", stderr)
        fflush(stderr)
    }
    _ = terminateObserver
    let closeObserver = NotificationCenter.default.addObserver(
        forName: NSWindow.willCloseNotification, object: window, queue: nil
    ) { _ in
        print("demo: window closed — exiting")
        fflush(stdout)
        exit(0)
    }
    _ = closeObserver

    // Self-checks + playback start are main-queue work — schedulable
    // normally because of the park-and-run structure below.
    for checkAt in [2.0, 5.0] {
        DispatchQueue.main.asyncAfter(deadline: .now() + checkAt) {
            MainActor.assumeIsolated {
                let item = engine.player.currentItem
                let layer = view.presentationLayer
                let frames = tap?.snapshot() ?? []
                let screenDescription = window.screen.map {
                    "\(Int($0.frame.width))×\(Int($0.frame.height))\($0 == NSScreen.main ? " (main)" : "")"
                } ?? "NONE"
                print(
                    "DEMO SELF-CHECK @\(Int(checkAt))s: "
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
                print(
                    "DEMO WINDOW-CHECK @\(Int(checkAt))s: "
                    + "visible=\(window.isVisible) "
                    + "onScreen(occlusion)=\(window.occlusionState.contains(.visible)) "
                    + "key=\(window.isKeyWindow) "
                    + "windowNumber=\(window.windowNumber) "
                    + "frame=\(Int(window.frame.origin.x)),\(Int(window.frame.origin.y)) \(Int(window.frame.width))×\(Int(window.frame.height)) "
                    + "screen=\(screenDescription) "
                    + "appActive=\(NSApp.isActive) "
                    + "policy=\(NSApp.activationPolicy() == .regular ? "regular" : "NOT-regular")"
                )
                fflush(stdout)
            }
        }
    }
    if selfCheck {
        DispatchQueue.main.asyncAfter(deadline: .now() + 6.5) {
            print("demo --selfcheck: auto-exit")
            fflush(stdout)
            exit(0)
        }
    }

    // Park-and-run (black/invisible-window incident, the load-bearing
    // structure): async main()'s frame IS a main-dispatch-queue job — any
    // `app.run()` inside it starves the queue (no DispatchQueue.main
    // blocks, no @MainActor tasks, no queue-.main observers — root cause
    // 2), and a hand-rolled event pump surfaces windows unreliably from a
    // CLI process (root cause 3: occlusion=false — ordered behind the
    // active Terminal under cooperative activation; the aggressive
    // orderFrontRegardless/.floating workaround intermittently drew a
    // silent exit(0) in shell-spawned contexts). Instead: schedule the
    // AppKit phase as a RUN-LOOP-SOURCE callout — not a queue callout —
    // then SUSPEND this frame forever. The suspension completes the
    // main-queue job, freeing the queue; the run loop then performs the
    // block, and app.run() executes with its full presentation/activation
    // machinery AND a serviceable main queue: dispatch drains, @MainActor
    // tasks run (R-key reload), engine observers fire.
    CFRunLoopPerformBlock(CFRunLoopGetMain(), CFRunLoopMode.commonModes.rawValue) {
        MainActor.assumeIsolated {
            window.makeKeyAndOrderFront(nil)
            window.makeFirstResponder(view)
            app.activate()
            engine.play()
            print("demo: playing \(ranges.count) ranges. Space=pause  R=reload(swap)  L=loop-here  Esc/Q=quit  (if the window isn't frontmost, click it or use Cmd-Tab)")
            fflush(stdout)
            app.run()
        }
    }
    CFRunLoopWakeUp(CFRunLoopGetMain())
    // Suspend forever — the process lives in app.run(); exit is via the
    // window-close observer, Q/Esc, or --selfcheck auto-exit.
    await withUnsafeContinuation { (_: UnsafeContinuation<Void, Never>) in }
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
