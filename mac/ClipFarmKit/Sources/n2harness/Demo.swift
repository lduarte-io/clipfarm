import AppKit
import AVFoundation
import CFMedia
import Foundation

/// `swift run n2harness demo [--uniform]` — the watch-session surface:
/// a real window + AVPlayerLayer playing a multi-source assembly through
/// the PlayerEngine. Lillian's plan-level verify is exactly this: "watch a
/// multi-source (camera + iPhone-style) assembly play gapless."
/// Space = play/pause, R = reload (swap-blink eyeball), L = 1.5s loop at
/// the current position (trim-loop eyeball), Esc/Q = quit.
@MainActor
func runDemo(env: HarnessEnv, uniform: Bool) async throws {
    let ranges: [PlayableRange]
    if uniform {
        ranges = [
            PlayableRange(url: env.footageFile("btc.0.4.mov"), startSec: 63.4, endSec: 68.9),
            PlayableRange(url: env.footageFile("btc.0.2.mov"), startSec: 30.13, endSec: 35.2),
            PlayableRange(url: env.footageFile("btc.0.0.mov"), startSec: 20.37, endSec: 25.5),
            PlayableRange(url: env.footageFile("btc.0.4.mov"), startSec: 121.2, endSec: 126.0),
        ]
    } else {
        ranges = [
            PlayableRange(url: env.footageFile("btc.0.4.mov"), startSec: 63.4, endSec: 68.9),
            PlayableRange(url: try await env.ensureFixture(FixtureSet.portrait), startSec: 3.0, endSec: 6.0),
            PlayableRange(url: env.footageFile("btc.0.2.mov"), startSec: 30.13, endSec: 34.2),
            PlayableRange(url: try await env.ensureFixture(FixtureSet.hlg), startSec: 8.0, endSec: 11.0),
            PlayableRange(url: env.footageFile("btc.0.0.mov"), startSec: 20.37, endSec: 24.5),
        ]
    }

    let app = NSApplication.shared
    app.setActivationPolicy(.regular)

    let engine = PlayerEngine()
    try await engine.load(ranges: ranges)

    let window = NSWindow(
        contentRect: NSRect(x: 0, y: 0, width: 1280, height: 720),
        styleMask: [.titled, .closable, .resizable],
        backing: .buffered, defer: false)
    window.title = "ClipFarm N2 demo — \(uniform ? "uniform (3× camera)" : "mixed (camera + portrait + HDR)") assembly"
    let view = PlayerHostView(engine: engine, ranges: ranges)
    window.contentView = view
    window.center()
    window.makeKeyAndOrderFront(nil)
    window.makeFirstResponder(view)
    app.activate()

    engine.play()
    print("demo: playing \(ranges.count) ranges. Space=pause  R=reload(swap)  L=loop-here  Esc/Q=quit")
    app.run()
}

@MainActor
final class PlayerHostView: NSView {
    private let engine: PlayerEngine
    private let ranges: [PlayableRange]
    private let playerLayer: AVPlayerLayer

    init(engine: PlayerEngine, ranges: [PlayableRange]) {
        self.engine = engine
        self.ranges = ranges
        self.playerLayer = AVPlayerLayer(player: engine.player)
        super.init(frame: .zero)
        wantsLayer = true
        layer?.backgroundColor = NSColor.black.cgColor
        playerLayer.videoGravity = .resizeAspect
        layer?.addSublayer(playerLayer)
    }

    required init?(coder: NSCoder) { fatalError() }

    override func layout() {
        super.layout()
        playerLayer.frame = bounds
    }

    override var acceptsFirstResponder: Bool { true }

    override func keyDown(with event: NSEvent) {
        switch event.charactersIgnoringModifiers?.lowercased() {
        case " ":
            engine.isPlaying ? engine.pause() : engine.play()
        case "r":
            Task { @MainActor in
                let at = engine.currentTimeSec
                try? await engine.load(ranges: ranges, at: at)
                engine.play()
            }
        case "l":
            let at = engine.currentTimeSec
            engine.loop(windowStartSec: max(0, at - 0.75), windowEndSec: at + 0.75)
            engine.play()
        case "q", "\u{1b}":
            NSApplication.shared.terminate(nil)
        default:
            super.keyDown(with: event)
        }
    }
}
