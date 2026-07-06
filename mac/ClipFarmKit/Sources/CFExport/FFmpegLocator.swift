import Foundation

/// The ffmpeg seam (D16): ffmpeg is reached ONLY through this locator —
/// nothing else in the app may know where the binary lives. v1 resolves from
/// a Settings override, then `PATH`, then the well-known install locations
/// (GUI apps don't inherit a shell `PATH`, so Homebrew paths are searched
/// explicitly). N19 swaps in a bundled, signed LGPL build behind this same
/// seam — a packaging change, not architecture.
public struct FFmpegLocator: Sendable {
    /// Settings-page override (lands with the N7 Settings work); checked
    /// first when set.
    public var overridePath: String?

    public init(overridePath: String? = nil) {
        self.overridePath = overridePath
    }

    /// Well-known locations searched after `PATH`: Homebrew (Apple Silicon),
    /// Homebrew (Intel), MacPorts, system.
    static let wellKnownDirectories = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/opt/local/bin",
        "/usr/bin",
    ]

    /// Resolves the ffmpeg binary, or `nil` when none is installed. Callers
    /// surface `nil` as a clear user-facing error (install ffmpeg / set the
    /// path in Settings) — never a crash.
    public func locate(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        fileManager: FileManager = .default
    ) -> URL? {
        if let overridePath, !overridePath.isEmpty {
            return fileManager.isExecutableFile(atPath: overridePath)
                ? URL(fileURLWithPath: overridePath)
                : nil
        }
        let pathDirectories = (environment["PATH"] ?? "").split(separator: ":").map(String.init)
        for directory in pathDirectories + Self.wellKnownDirectories {
            let candidate = (directory as NSString).appendingPathComponent("ffmpeg")
            if fileManager.isExecutableFile(atPath: candidate) {
                return URL(fileURLWithPath: candidate)
            }
        }
        return nil
    }
}
