import AppKit
import CoreVideo
import Foundation
import VideoToolbox

/// Reads the self-identifying content `MediaFixtureRenderer` draws, from
/// delivered (decoded/rendered) BGRA pixel buffers. Sampling is
/// proportional, so it survives scaling; it does NOT survive rotation or
/// pillarboxing (the rotation gate probes markers with computed geometry
/// instead).
public enum PixelProbe {
    /// Decode the 16-bit frame index from the top bit row. Returns nil when
    /// the content doesn't look like a fixture bit row (low contrast) —
    /// e.g. real footage or a mid-swap black frame.
    public static func frameIndex(in pixelBuffer: CVPixelBuffer) -> Int? {
        let decoded: Int?? = withBGRA(pixelBuffer) { base, width, height, bytesPerRow in
            let y = height / 16  // vertical middle of the top-eighth bit row
            var index = 0
            for block in 0..<16 {
                // Center of each block, averaged over a few x offsets.
                let cx = width * (2 * block + 1) / 32
                var sum = 0
                var n = 0
                for dx in -2...2 {
                    let x = min(max(cx + dx, 0), width - 1)
                    let p = base.advanced(by: y * bytesPerRow + x * 4)
                        .assumingMemoryBound(to: UInt8.self)
                    sum += (Int(p[0]) + Int(p[1]) + Int(p[2])) / 3
                    n += 1
                }
                let luma = sum / n
                // 16/235 drawn; anything ambiguous ⇒ not a fixture frame.
                if luma > 160 {
                    index |= 1 << (15 - block)
                } else if luma > 90 {
                    return Int?.none  // ambiguous — not a fixture frame
                }
            }
            return index
        }
        return decoded ?? nil
    }

    /// Mean (r, g, b) over a normalized rect (0...1 coordinates).
    public static func meanRGB(
        in pixelBuffer: CVPixelBuffer,
        rect: CGRect = CGRect(x: 0.3, y: 0.4, width: 0.4, height: 0.2)
    ) -> (r: Double, g: Double, b: Double) {
        withBGRA(pixelBuffer) { base, width, height, bytesPerRow in
            let x0 = Int(rect.minX * CGFloat(width))
            let x1 = max(x0 + 1, Int(rect.maxX * CGFloat(width)))
            let y0 = Int(rect.minY * CGFloat(height))
            let y1 = max(y0 + 1, Int(rect.maxY * CGFloat(height)))
            var r = 0.0, g = 0.0, b = 0.0
            var n = 0.0
            var y = y0
            while y < y1 {
                let row = base.advanced(by: y * bytesPerRow).assumingMemoryBound(to: UInt8.self)
                var x = x0
                while x < x1 {
                    b += Double(row[x * 4])
                    g += Double(row[x * 4 + 1])
                    r += Double(row[x * 4 + 2])
                    n += 1
                    x += 2
                }
                y += 2
            }
            guard n > 0 else { return (0, 0, 0) }
            return (r / n, g / n, b / n)
        } ?? (0, 0, 0)
    }

    /// Fixture content is gray ≥ ~100 everywhere except the bit/marker
    /// strips; a swap-blink black frame is unmistakable.
    public static func isBlack(_ pixelBuffer: CVPixelBuffer) -> Bool {
        let mean = meanRGB(in: pixelBuffer)
        return (mean.r + mean.g + mean.b) / 3 < 20
    }

    /// Dump a delivered frame for Lillian's watch session.
    public static func writePNG(_ pixelBuffer: CVPixelBuffer, to url: URL) throws {
        var cgImage: CGImage?
        VTCreateCGImageFromCVPixelBuffer(pixelBuffer, options: nil, imageOut: &cgImage)
        guard let cgImage else {
            throw NSError(domain: "PixelProbe", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "VTCreateCGImageFromCVPixelBuffer failed"
            ])
        }
        let rep = NSBitmapImageRep(cgImage: cgImage)
        guard let data = rep.representation(using: .png, properties: [:]) else {
            throw NSError(domain: "PixelProbe", code: 2, userInfo: [
                NSLocalizedDescriptionKey: "PNG encode failed"
            ])
        }
        try data.write(to: url)
    }

    private static func withBGRA<T>(
        _ pixelBuffer: CVPixelBuffer,
        _ body: (UnsafeMutableRawPointer, Int, Int, Int) -> T
    ) -> T? {
        guard CVPixelBufferGetPixelFormatType(pixelBuffer) == kCVPixelFormatType_32BGRA else {
            return nil
        }
        CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(pixelBuffer) else { return nil }
        return body(
            base,
            CVPixelBufferGetWidth(pixelBuffer),
            CVPixelBufferGetHeight(pixelBuffer),
            CVPixelBufferGetBytesPerRow(pixelBuffer)
        )
    }
}
