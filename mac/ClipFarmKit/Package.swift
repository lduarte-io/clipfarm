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
        .package(url: "https://github.com/groue/GRDB.swift.git", from: "7.0.0")
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
            dependencies: ["CFDomain"],
            swiftSettings: kitSwiftSettings
        ),

        .testTarget(
            name: "CFDomainTests",
            dependencies: ["CFDomain"],
            swiftSettings: kitSwiftSettings
        ),
        .testTarget(
            name: "CFStoreTests",
            dependencies: ["CFStore"],
            swiftSettings: kitSwiftSettings
        ),
        .testTarget(
            name: "CFMediaTests",
            dependencies: ["CFMedia"],
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
