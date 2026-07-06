# QUESTIONS — batched for Lillian

Appended by implementer/coordinator sessions during autonomous runs (`/run-phase`). The coordinator surfaces open items at every hard-stop checkpoint (immediately, if blocking). When Lillian answers, the item moves to **Answered** with the resolution and where it landed; any PROVISIONAL implementation it references flips to final (or gets reworked) at that point.

Format per item:

- **[phase · date]** The question — options considered → what was provisionally implemented (file / phase-entry reference).

## Open

- **[N0 · 2026-07-05]** GRDB "pinned" mechanics — `exact: "7.x.y"` in Package.swift vs `from: "7.0.0"` (major-locked) with the exact version pinned by the committed `Package.resolved` vs vendoring → **implemented `from: "7.0.0"` + committed `Package.resolved`, resolved at 7.11.1** (standard SPM practice; `exact:` would block GRDB patch releases and required guessing the latest 7.x). PROVISIONAL — fine to leave unless you want a hard `exact:` pin. (`mac/ClipFarmKit/Package.swift`; PHASES.md → N0.)
- **[N0 · 2026-07-05]** Package product shape — one umbrella `ClipFarmKit` library product exporting all five targets vs five separate products → **implemented the umbrella product** (one product dependency in the pbxproj; the app still imports modules individually, e.g. `import CFDomain`). PROVISIONAL. (`mac/ClipFarmKit/Package.swift`.)
- **[N0 · 2026-07-05]** Intra-Kit target dependency graph — plan §2.2 fixes CFStore→GRDB and CFDomain→nothing but doesn't draw the rest (e.g. CFExport→CFMedia) → **implemented the minimal graph: every non-domain target depends only on CFDomain** (+ GRDB for CFStore); edges get added when a phase actually needs them (one-line change). PROVISIONAL. (`mac/ClipFarmKit/Package.swift`.)
- **[N0 · 2026-07-05]** Swift language mode on the app target — "Swift 6.2" names the compiler, not a language mode; `SWIFT_VERSION=5.0` (Apple's migration-friendly default) vs `6.0` (strict concurrency) → **implemented 6.0**, matching the Kit targets (which default to mode 6 under swift-tools 6.2) per the "keep the app/package boundary symmetric" policy. PROVISIONAL. (`mac/ClipFarm.xcodeproj/project.pbxproj`.)

## Answered

*(none yet)*
