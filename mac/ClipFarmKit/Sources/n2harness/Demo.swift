import AppKit
import AVFoundation
import CFMedia
import Foundation

/// `swift run n2harness demo [--real]` — the watch-session surface:
/// a real window + AVPlayerLayer playing a multi-source assembly through
/// the PlayerEngine. Lillian's plan-level verify is exactly this: "watch a
/// multi-source (camera + iPhone-style) assembly play gapless."
/// Default assembly = footage-inbox files interleaved with the HLG-HDR and
/// portrait fixtures (exercises D29 + D32 in one watch); `--real` restricts
/// it to inbox files only. Space = play/pause, R = reload (swap-blink
/// eyeball), L = 1.5s loop at the current position (trim-loop eyeball),
/// Esc/Q = quit.
@MainActor
func runDemo(env: HarnessEnv, realOnly: Bool) async throws {
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

    let app = NSApplication.shared
    app.setActivationPolicy(.regular)

    let engine = PlayerEngine()
    try await engine.load(ranges: ranges)

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
