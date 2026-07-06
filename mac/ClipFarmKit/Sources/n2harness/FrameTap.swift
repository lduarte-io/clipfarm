import AVFoundation
import CFMediaTestSupport
import CoreMedia
import CoreVideo
import Foundation
import QuartzCore

/// Frame-delivery instrumentation (PHASES.md → N2 PROVISIONAL 2): an
/// `AVPlayerItemVideoOutput` polled from a dedicated 2 kHz thread records
/// (hostTime, presentationTime, frameIndex?, isBlack) for every delivered
/// frame — decode-level ground truth for seam drops, swap blinks, loop
/// restarts, and frame accuracy.
final class FrameTap: @unchecked Sendable {
    struct Sample: Sendable {
        let hostTime: Double
        let itemTimeSec: Double
        let frameIndex: Int?
        let isBlack: Bool
    }

    private let output: AVPlayerItemVideoOutput
    private let lock = NSLock()
    private var samples: [Sample] = []
    private var running = false
    private var thread: Thread?
    private let decode: Bool
    private var pngRequest: (time: Double, url: URL)?

    /// - Parameter decode: probe fixture content (frame index / blackness).
    ///   Costs a few µs per frame; disable for pure timing runs on real
    ///   footage if desired.
    @MainActor
    init(item: AVPlayerItem, decode: Bool = true) {
        self.decode = decode
        output = AVPlayerItemVideoOutput(pixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA
        ])
        item.add(output)
    }

    func start() {
        lock.lock()
        defer { lock.unlock() }
        guard !running else { return }
        running = true
        let thread = Thread { [weak self] in
            self?.pollLoop()
        }
        thread.qualityOfService = .userInteractive
        thread.name = "n2harness.frametap"
        thread.start()
        self.thread = thread
    }

    func stop() {
        lock.lock()
        running = false
        lock.unlock()
    }

    func snapshot() -> [Sample] {
        lock.lock()
        defer { lock.unlock() }
        return samples
    }

    func clear() {
        lock.lock()
        samples.removeAll()
        lock.unlock()
    }

    /// Dump the next delivered frame at/after `itemTime` as a PNG.
    func requestPNG(atItemTime time: Double, to url: URL) {
        lock.lock()
        pngRequest = (time, url)
        lock.unlock()
    }

    private func pollLoop() {
        while true {
            lock.lock()
            let live = running
            lock.unlock()
            guard live else { return }

            let host = CACurrentMediaTime()
            let itemTime = output.itemTime(forHostTime: host)
            if output.hasNewPixelBuffer(forItemTime: itemTime) {
                var display = CMTime.invalid
                if let pixelBuffer = output.copyPixelBuffer(
                    forItemTime: itemTime, itemTimeForDisplay: &display
                ) {
                    let index = decode ? PixelProbe.frameIndex(in: pixelBuffer) : nil
                    let black = decode ? PixelProbe.isBlack(pixelBuffer) : false
                    let sample = Sample(
                        hostTime: CACurrentMediaTime(),
                        itemTimeSec: display.seconds,
                        frameIndex: index,
                        isBlack: black
                    )
                    lock.lock()
                    samples.append(sample)
                    if let request = pngRequest, display.seconds >= request.time {
                        pngRequest = nil
                        try? PixelProbe.writePNG(pixelBuffer, to: request.url)
                    }
                    lock.unlock()
                }
            }
            usleep(500)  // 2 kHz — ±0.5 ms timing resolution
        }
    }
}

/// Await the first tap sample matching `predicate`, with timeout.
func awaitSample(
    _ tap: FrameTap,
    timeoutSec: Double = 5.0,
    where predicate: @escaping (FrameTap.Sample) -> Bool
) async -> FrameTap.Sample? {
    let deadline = CACurrentMediaTime() + timeoutSec
    var seen = 0
    while CACurrentMediaTime() < deadline {
        let samples = tap.snapshot()
        if samples.count > seen {
            for sample in samples[seen...] where predicate(sample) {
                return sample
            }
            seen = samples.count
        }
        try? await Task.sleep(for: .milliseconds(2))
    }
    return nil
}
