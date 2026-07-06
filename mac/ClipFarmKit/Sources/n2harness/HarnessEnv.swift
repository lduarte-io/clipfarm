import CFMedia
import CFMediaTestSupport
import Foundation

/// Shared environment for every gate: working directory, the footage inbox,
/// the synthetic fixture set (PHASES.md → N2 PROVISIONAL 1), report sink.
///
/// Footage comes from the inbox `~/ClipFarm/Footage/` (D34 / spec
/// amendment 14) — a managed working folder Lillian populates herself.
/// The harness only ever READS it (its outputs are all regenerable and
/// land in the workdir); real-file gate legs adapt to whatever is in the
/// inbox and fall back to synthetic fixtures — flagged in the report —
/// when no qualifying file exists. Footage anywhere else (including the
/// retired `~/Desktop/AdAstra/…` dogfood path) is off-limits.
struct HarnessEnv {
    let workdir: URL
    let footage: URL

    static let defaultFootage = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("ClipFarm/Footage")

    static let defaultWorkdir = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Caches/ClipFarm-N2Gates")

    init(arguments: [String]) throws {
        var workdir = Self.defaultWorkdir
        var footage = Self.defaultFootage
        var iterator = arguments.makeIterator()
        while let arg = iterator.next() {
            switch arg {
            case "--workdir": workdir = URL(fileURLWithPath: iterator.next() ?? "")
            case "--footage": footage = URL(fileURLWithPath: iterator.next() ?? "")
            default: break
            }
        }
        self.workdir = workdir
        self.footage = footage
        try FileManager.default.createDirectory(
            at: workdir.appendingPathComponent("reports"), withIntermediateDirectories: true)
        try FileManager.default.createDirectory(
            at: workdir.appendingPathComponent("export"), withIntermediateDirectories: true)
        try FileManager.default.createDirectory(
            at: workdir.appendingPathComponent("frames"), withIntermediateDirectories: true)
        try FileManager.default.createDirectory(
            at: workdir.appendingPathComponent("audio"), withIntermediateDirectories: true)
    }

    var fixtureDir: URL { workdir.appendingPathComponent("fixtures") }

    /// Video files currently in the footage inbox, sorted by name.
    func realVideoFiles() -> [URL] {
        let extensions: Set<String> = ["mov", "mp4", "m4v"]
        let contents = (try? FileManager.default.contentsOfDirectory(
            at: footage, includingPropertiesForKeys: nil)) ?? []
        return contents
            .filter { extensions.contains($0.pathExtension.lowercased()) }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    /// Inbox files with probed metadata — durations drive every gate's
    /// range layout (files change between runs; nothing is hardcoded).
    func probedRealFiles() async -> [(url: URL, meta: SourceMetadata)] {
        var probed: [(url: URL, meta: SourceMetadata)] = []
        for url in realVideoFiles() {
            if let meta = try? await MetadataProbe.probe(url: url) {
                probed.append((url, meta))
            }
        }
        return probed
    }

    func ensureFixture(_ spec: MediaFixtureSpec) async throws -> URL {
        let url = try await MediaFixtureRenderer.render(spec, in: fixtureDir)
        return url
    }

    /// Append a gate's findings to its report file AND stdout — the
    /// closeout gate table copies from these.
    func report(_ gate: String, _ lines: [String]) {
        let text = lines.joined(separator: "\n")
        print(text)
        let url = workdir.appendingPathComponent("reports/\(gate).md")
        let stamped = "## \(gate) — \(ISO8601DateFormatter().string(from: Date()))\n\n\(text)\n\n"
        if let existing = try? String(contentsOf: url, encoding: .utf8) {
            try? (existing + stamped).write(to: url, atomically: true, encoding: .utf8)
        } else {
            try? stamped.write(to: url, atomically: true, encoding: .utf8)
        }
    }
}

/// The N2 fixture set (rendered on demand; ~2 min total on first run).
enum FixtureSet {
    /// Long-GOP H.264 1080p, keyframes every 3 s.
    static let h264A = MediaFixtureSpec(
        name: "n2-h264-a-1080p30-kf90", codec: .h264, width: 1920, height: 1080,
        fps: 30, durationSec: 60, keyframeIntervalFrames: 90,
        grayLevel: 118, averageBitRate: 8_000_000)
    /// Second H.264 file for two-file splices, different gray.
    static let h264B = MediaFixtureSpec(
        name: "n2-h264-b-1080p30-kf90", codec: .h264, width: 1920, height: 1080,
        fps: 30, durationSec: 60, keyframeIntervalFrames: 90,
        grayLevel: 140, averageBitRate: 8_000_000)
    /// Long-GOP 4K HEVC, keyframes every 4 s (worst-case trim-loop gate).
    static let hevc4K = MediaFixtureSpec(
        name: "n2-hevc-4k30-kf120", codec: .hevc, width: 3840, height: 2160,
        fps: 30, durationSec: 45, keyframeIntervalFrames: 120,
        grayLevel: 125, averageBitRate: 25_000_000)
    /// HEVC 1080p sibling (uniform-geometry frame-accuracy runs).
    static let hevc1080 = MediaFixtureSpec(
        name: "n2-hevc-1080p30-kf120", codec: .hevc, width: 1920, height: 1080,
        fps: 30, durationSec: 45, keyframeIntervalFrames: 120,
        grayLevel: 132, averageBitRate: 10_000_000)
    /// All-intra ProRes 422 (kept 720p/20s — ProRes bitrates are huge).
    static let proRes = MediaFixtureSpec(
        name: "n2-prores-720p30", codec: .proRes422, width: 1280, height: 720,
        fps: 30, durationSec: 20, grayLevel: 130)
    /// iPhone-HDR-style: 10-bit HLG BT.2020 HEVC (D29 gate material).
    static let hlg = MediaFixtureSpec(
        name: "n2-hlg-1080p30", codec: .hevc10HLG, width: 1920, height: 1080,
        fps: 30, durationSec: 30, keyframeIntervalFrames: 60,
        grayLevel: 118, averageBitRate: 12_000_000)
    /// iPhone-portrait-style: encoded landscape + 90° track transform.
    static let portrait = MediaFixtureSpec(
        name: "n2-portrait-h264-1080p30", codec: .h264, width: 1920, height: 1080,
        fps: 30, durationSec: 20, keyframeIntervalFrames: 60, rotated90: true,
        grayLevel: 118, averageBitRate: 6_000_000)
    /// Fade-gate audio: sine bed + 1 kHz bursts at every whole 2 s (LPCM).
    static let bursts = MediaFixtureSpec(
        name: "n2-bursts-720p30", codec: .h264, width: 1280, height: 720,
        fps: 30, durationSec: 30, keyframeIntervalFrames: 60,
        grayLevel: 120, averageBitRate: 4_000_000, audio: .sineWithBursts)

    static let all: [MediaFixtureSpec] = [
        h264A, h264B, hevc4K, hevc1080, proRes, hlg, portrait, bursts,
    ]
}

// MARK: - Small numeric helpers

enum Stats {
    static func percentile(_ values: [Double], _ p: Double) -> Double {
        guard !values.isEmpty else { return .nan }
        let sorted = values.sorted()
        let rank = p / 100 * Double(sorted.count - 1)
        let low = Int(rank.rounded(.down))
        let high = Int(rank.rounded(.up))
        if low == high { return sorted[low] }
        let fraction = rank - Double(low)
        return sorted[low] * (1 - fraction) + sorted[high] * fraction
    }

    static func summary(_ values: [Double], unit: String = "ms", scale: Double = 1000) -> String {
        guard !values.isEmpty else { return "n=0" }
        let scaled = values.map { $0 * scale }
        return String(
            format: "n=%d  p50=%.2f%@  p95=%.2f%@  max=%.2f%@",
            values.count,
            percentile(scaled, 50), unit,
            percentile(scaled, 95), unit,
            scaled.max()!, unit
        )
    }
}

func fmt(_ value: Double, _ digits: Int = 2) -> String {
    String(format: "%.\(digits)f", value)
}

/// `count` ranges of `length` seconds spread across a file's usable span.
/// Starts carry an odd sub-second phase so they never land on whole
/// seconds (recorder keyframes sit on regular grids — cuts stay
/// deliberately non-keyframe-aligned).
func spreadRanges(url: URL, durationSec: Double, count: Int, length: Double) -> [PlayableRange] {
    let lo = min(1.37, durationSec * 0.05)
    let hi = durationSec - length - 0.25
    guard hi > lo, count > 0 else { return [] }
    let step = count > 1 ? (hi - lo) / Double(count) : 0
    return (0..<count).map { i in
        let start = min(lo + Double(i) * step + 0.113, hi)
        return PlayableRange(url: url, startSec: start, endSec: start + length)
    }
}

/// Round-robin interleave so consecutive playback ranges alternate source
/// files wherever possible (cross-file seams are the harder case).
func roundRobin<T>(_ groups: [[T]]) -> [T] {
    var result: [T] = []
    var index = 0
    var appended = true
    while appended {
        appended = false
        for group in groups where index < group.count {
            result.append(group[index])
            appended = true
        }
        index += 1
    }
    return result
}
