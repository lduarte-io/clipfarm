// swift-tools-version: 6.2
import PackageDescription

// Isolation policy (SE-0466, mac/CLAUDE.md): the app target is MainActor-default;
// every ClipFarmKit target is EXPLICITLY nonisolated-default — packages do not
// inherit the Xcode default, and Kit targets must never silently serialize onto
// the main thread. `.defaultIsolation(nil)` == nonisolated default isolation.
//
// Approachable Concurrency symmetry: the app target sets
// SWIFT_APPROACHABLE_CONCURRENCY=YES. Under Swift 6 language mode (the default
// for swift-tools 6.2) most of that bundle is already on; the two deltas are
// enabled explicitly here so the app/package boundary stays symmetric
// (SE-0461 NonisolatedNonsendingByDefault, SE-0470 InferIsolatedConformances).
let kitSwiftSettings: [SwiftSetting] = [
    .defaultIsolation(nil),
    .enableUpcomingFeature("NonisolatedNonsendingByDefault"),
    .enableUpcomingFeature("InferIsolatedConformances"),
]

let package = Package(
    name: "ClipFarmKit",
    platforms: [
        .macOS(.v26)
    ],
    products: [
        .library(
            name: "ClipFarmKit",
            targets: ["CFDomain", "CFStore", "CFMedia", "CFLLM", "CFExport"]
        )
    ],
    dependencies: [
        // Pinned via the committed Package.resolved (major locked to 7).
        .package(url: "https://github.com/groue/GRDB.swift.git", from: "7.0.0"),
        // Approved by Lillian 2026-07-06 (QUESTIONS.md → Answered): pinned
        // EXACTLY to 1.0.0-beta.1 (pre-1.0 churn — D16); bumping to the 1.0
        // GA tag is a deliberate reviewed step, never a float. Apache-2.0.
        .package(url: "https://github.com/swiftlang/swift-subprocess.git", exact: "1.0.0-beta.1"),
    ],
    targets: [
        // CFDomain: pure logic, ZERO dependencies. Keep it that way.
        .target(
            name: "CFDomain",
            swiftSettings: kitSwiftSettings
        ),
        .target(
            name: "CFStore",
            dependencies: [
                "CFDomain",
                .product(name: "GRDB", package: "GRDB.swift"),
            ],
            swiftSettings: kitSwiftSettings
        ),
        .target(
            name: "CFMedia",
            dependencies: ["CFDomain"],
            swiftSettings: kitSwiftSettings
        ),
        .target(
            name: "CFLLM",
            dependencies: ["CFDomain"],
            swiftSettings: kitSwiftSettings
        ),
        .target(
            name: "CFExport",
            dependencies: [
                "CFDomain",
                // ffmpeg subprocess seam (D16): ffmpeg is reached ONLY via
                // FFmpegLocator in this target — never from anywhere else.
                .product(name: "Subprocess", package: "swift-subprocess"),
            ],
            swiftSettings: kitSwiftSettings
        ),
        // Fixture builders shared by test targets. Not part of the library
        // product — never ships in the app.
        .target(
            name: "CFTestSupport",
            dependencies: ["CFDomain"],
            swiftSettings: kitSwiftSettings
        ),
        // Synthetic-media rendering (AVAssetWriter fixtures with
        // frame-index-encoded content) + pixel probing, shared by
        // CFMediaTests and the N2 gate harness. Never ships.
        .target(
            name: "CFMediaTestSupport",
            swiftSettings: kitSwiftSettings
        ),
        // N2 debug harness (PHASES.md → N2 PROVISIONAL 3): drives the gate
        // measurements from the CLI (`swift run n2harness <gate>`).
        // Debug tooling — never ships, not part of the library product.
        .executableTarget(
            name: "n2harness",
            dependencies: ["CFDomain", "CFMedia", "CFMediaTestSupport"],
            swiftSettings: kitSwiftSettings
        ),

        .testTarget(
            name: "CFDomainTests",
            dependencies: ["CFDomain", "CFTestSupport"],
            // segmentation-golden.json: reference-implementation output for
            // the cross-implementation golden master (scripts/gen_segmentation_golden.py).
            resources: [.copy("Resources")],
            swiftSettings: kitSwiftSettings
        ),
        .testTarget(
            name: "CFStoreTests",
            dependencies: [
                "CFStore",
                "CFTestSupport",
                // Raw-SQL schema assertions (pragmas, index backstops).
                .product(name: "GRDB", package: "GRDB.swift"),
            ],
            swiftSettings: kitSwiftSettings
        ),
        .testTarget(
            name: "CFMediaTests",
            dependencies: ["CFMedia", "CFMediaTestSupport"],
            swiftSettings: kitSwiftSettings
        ),
        .testTarget(
            name: "CFLLMTests",
            dependencies: ["CFLLM"],
            swiftSettings: kitSwiftSettings
        ),
        .testTarget(
            name: "CFExportTests",
            dependencies: ["CFExport"],
            swiftSettings: kitSwiftSettings
        ),
    ]
)
